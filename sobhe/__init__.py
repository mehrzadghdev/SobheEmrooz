"""Flask application factory; schema and account setup are explicit CLI operations."""
import hashlib
import os
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, g, jsonify, render_template, request, session
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import HTTPException
from .models import User, Setting, db

@event.listens_for(Engine, 'connect')
def configure_sqlite(connection, _):
    if isinstance(connection, sqlite3.Connection):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=10000')


def create_app(config=None, *, instance_path=None):
    data_dir = instance_path or os.environ.get('SOBHE_DATA_DIR')
    if data_dir and not Path(data_dir).is_absolute():
        raise ValueError('SOBHE_DATA_DIR must be an absolute path.')
    app = Flask(__name__, instance_relative_config=True, instance_path=data_dir,
                template_folder='../templates', static_folder='../static')
    root = Path(app.instance_path)
    root.mkdir(parents=True, exist_ok=True)
    secret = (config or {}).get('SECRET_KEY') or os.environ.get('SOBHE_SECRET_KEY')
    if not secret:
        secret_path = root / '.session-secret'
        try:
            with secret_path.open('x') as output:
                os.chmod(secret_path, 0o600)
                output.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        secret = secret_path.read_text().strip()
    app.config.update(
        SECRET_KEY=secret, SQLALCHEMY_DATABASE_URI=os.environ.get('SOBHE_DATABASE_URL', 'sqlite:///sobhe.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False, STORAGE_ROOT=str(root / 'storage'),
        MAX_CONTENT_LENGTH=12 * 1024 * 1024, CSRF_ENABLED=True,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.environ.get('SOBHE_ENV') == 'production',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if config:
        app.config.update(config)
    db.init_app(app)

    def csrf_token():
        session.setdefault('_csrf', secrets.token_urlsafe(32))
        return session['_csrf']

    @app.before_request
    def protect_request():
        g.user = None
        g.staged_files = []
        if request.endpoint == 'static' or (request.endpoint or '').startswith('pwa.'):
            return
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            supplied = request.form.get('csrf_token', '') or request.headers.get('X-CSRF-Token', '')
            if app.config['CSRF_ENABLED'] and (not supplied or not secrets.compare_digest(
                    supplied.encode(), session.get('_csrf', '').encode())):
                abort(400, 'درخواست منقضی شده؛ صفحه را تازه کنید.')
            try:
                db.session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            except OperationalError:
                db.session.rollback()
                abort(503, 'سامانه مشغول است؛ دوباره تلاش کنید.')
        user_id = session.get('user_id')
        if user_id:
            user = db.session.get(User, user_id)
            if user and user.active and user.auth_version == session.get('auth_version'):
                g.user = user
            else:
                session.clear()

    @app.teardown_request
    def finish_request(error):
        db.session.rollback()
        for path in getattr(g, 'staged_files', []):
            path.unlink(missing_ok=True)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
        if request.endpoint != 'static' and not (request.endpoint or '').startswith('pwa.'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    from .constants import AD_TYPES, COLUMNS, DATE_FIELDS, PRINT_STATUSES, PROVINCES, ROLES, WEIGHTS
    from .services import boxes, category_labels, decimal_string, jalali, period_key, period_label, today
    from .web import bp
    app.register_blueprint(bp)
    from .commands import register_commands
    register_commands(app)
    from .workbook_import import register_import_command
    register_import_command(app)
    app.jinja_env.filters.update(boxes=boxes, jalali=jalali, decimal=decimal_string,
                                 number=lambda value: f'{value:,}' if value is not None else '—')

    @app.context_processor
    def context():
        if (request.endpoint or '').startswith('pwa.'):
            return {}
        return dict(csrf_token=csrf_token, category_labels=category_labels(), roles=ROLES,
                    weights=WEIGHTS, ad_types=AD_TYPES, print_statuses=PRINT_STATUSES,
                    provinces=PROVINCES, columns=COLUMNS, date_fields=DATE_FIELDS,
                    today=today(), current_period_key=period_key(), period_label=period_label,
                    demo_mode=db.session.get(Setting, 'demo_mode') is not None)

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {400: 'درخواست معتبر نیست.', 403: 'شما اجازه انجام این عملیات را ندارید.',
                    404: 'رکورد یا صفحه موردنظر پیدا نشد.', 405: 'روش درخواست مجاز نیست.',
                    413: 'حجم فایل یا درخواست بیش از حد مجاز است.', 503: 'سامانه مشغول است؛ دوباره تلاش کنید.'}
        message = error.description if any('\u0600' <= c <= '\u06ff' for c in error.description) else messages.get(error.code, 'خطای سامانه؛ دوباره تلاش کنید.')
        if request.path.startswith('/api/'):
            return jsonify(error=message), error.code
        return render_template('error.html', message=message, code=error.code), error.code

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template('error.html', message='خطای سامانه؛ اطلاعات ذخیره نشد. دوباره تلاش کنید.', code=500), 500

    # Content-based versions preserve the sample's static-cache convention.
    versions = {path.relative_to(app.static_folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()[:12]
                for path in Path(app.static_folder).rglob('*') if path.is_file()}

    @app.url_defaults
    def asset_version(endpoint, values):
        if endpoint == 'static' and values.get('filename') in versions:
            values.setdefault('v', versions[values['filename']])

    from .pwa import init_app as init_pwa
    init_pwa(app, versions)

    return app

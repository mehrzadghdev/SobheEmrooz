"""Install metadata and a public offline screen, independent of accounts and data."""
import hashlib
import json
from pathlib import Path

from flask import Blueprint, current_app, jsonify, make_response, render_template, send_from_directory, url_for

bp = Blueprint('pwa', __name__)
CORE_ASSETS = (
    'css/app.css', 'css/dashboard.css', 'css/workspaces.css', 'css/fonts.css', 'css/pwa.css',
    'js/theme.js', 'js/button-loading.js', 'js/pwa.js', 'js/app.js', 'js/page-navigation.js',
    'css/report-charts.css', 'js/report-charts.js', 'css/tables.css', 'js/tables.js',
    'js/jalali-date.js', 'js/jalali-picker.js', 'js/allocation.js',
    'fonts/woff2/IRANYekanWebRegular.woff2', 'fonts/woff2/IRANYekanWebMedium.woff2',
    'fonts/woff2/IRANYekanWebBold.woff2', 'icon.svg',
    'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png',
)


def init_app(app, asset_versions):
    templates = Path(app.root_path) / app.template_folder
    version = hashlib.sha256(json.dumps(asset_versions, sort_keys=True).encode())
    version.update(Path(__file__).read_bytes())
    for filename in ('offline.html', 'service-worker.js', 'partials/pwa.html'):
        version.update((templates / filename).read_bytes())
    app.extensions['pwa'] = {'version': version.hexdigest()[:16]}
    app.register_blueprint(bp)


@bp.get('/manifest.webmanifest')
def manifest():
    home = url_for('web.dashboard')
    response = jsonify(
        id=home, name='سامانه آگهی‌های دولتی · صبح امروز', short_name='صبح امروز',
        lang='fa-IR', dir='rtl', description='مدیریت و توزیع آگهی‌های دولتی و گزارش سهم رسانه‌ها',
        start_url=home, scope=home, display='standalone',
        background_color='#fafbfc', theme_color='#da1214',
        categories=['business', 'productivity'], prefer_related_applications=False,
        icons=[
            dict(src=url_for('static', filename='icons/icon-192.png'), sizes='192x192', type='image/png', purpose='any'),
            dict(src=url_for('static', filename='icons/icon-512.png'), sizes='512x512', type='image/png', purpose='any'),
            dict(src=url_for('static', filename='icons/icon-512.png'), sizes='512x512', type='image/png', purpose='maskable'),
        ],
    )
    response.mimetype = 'application/manifest+json'
    response.headers['Cache-Control'] = 'no-cache'
    return response


@bp.get('/service-worker.js')
def service_worker():
    urls = [url_for('static', filename=filename) for filename in CORE_ASSETS]
    # Relative font references in fonts.css have no version query string.
    urls = [url.split('?', 1)[0] if '/fonts/' in url else url for url in urls]
    urls.append(url_for('pwa.manifest'))
    response = make_response(render_template(
        'service-worker.js', version=current_app.extensions['pwa']['version'],
        offline_url=url_for('pwa.offline'), precache=urls,
        public_paths=[url.split('?', 1)[0] for url in urls],
    ))
    response.headers['Content-Type'] = 'application/javascript; charset=utf-8'
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Service-Worker-Allowed'] = url_for('web.dashboard')
    return response


@bp.get('/offline')
def offline():
    response = make_response(render_template('offline.html'))
    response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
    return response


@bp.get('/favicon.ico')
def favicon():
    response = send_from_directory(current_app.static_folder, 'icons/icon-192.png', mimetype='image/png')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response

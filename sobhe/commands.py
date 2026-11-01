"""Explicit SQLite initialization, personal account bootstrap, backup, and demo data."""
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import click
from flask import current_app
from sqlalchemy import inspect
from werkzeug.security import generate_password_hash

from .allocations import update_recovery
from .constants import CATEGORY_LABELS
from .models import (Advertisement, AuditEvent, Organization, Outlet, OutletTarget, Period,
                     SchemaVersion, Setting, Subject, User, db)
from .services import period_bounds, period_key, today


def migrate():
    tables = inspect(db.engine).get_table_names()
    if tables:
        if 'schema_version' not in tables:
            raise click.ClickException('Unversioned database; refusing to alter it. Use a new SOBHE_DATA_DIR.')
        versions = [row.version for row in SchemaVersion.query.all()]
        if versions != [1]:
            raise click.ClickException('Unsupported schema version; no changes made.')
        return 'Schema version 1 is already current.'
    db.create_all()
    db.session.add(SchemaVersion(version=1))
    for key in ('local_one', 'local_two'):
        db.session.add(Setting(key='label_' + key, value=CATEGORY_LABELS[key]))
    for name in ('مناقصه', 'مزایده', 'ثبت شرکت', 'ابلاغیه قضایی', 'اطلاعیه'):
        db.session.add(Subject(name=name, active=True))
    db.session.commit()
    with db.engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    return 'Initialized schema version 1. Create your personal administrator with create-admin.'


def backup(destination):
    database = Path(db.engine.url.database)
    if not database.is_file():
        raise click.ClickException('Database does not exist; initialize it first.')
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise click.ClickException('Backup destination already exists; choose a new file.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sobhe-backup-') as temporary:
        copy = Path(temporary) / 'sobhe.db'
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(copy)) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise click.ClickException('Backup integrity check failed.')
        with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(copy, 'sobhe.db')
            storage = Path(current_app.config['STORAGE_ROOT'])
            if storage.exists():
                for path in storage.rglob('*'):
                    if path.is_file() and not path.is_symlink() and path.resolve() != destination:
                        archive.write(path, 'storage/' + path.relative_to(storage).as_posix())
    os.chmod(destination, 0o600)
    return destination


def seed_demo():
    if Advertisement.query.first() or Outlet.query.first() or Period.query.first():
        raise click.ClickException('Demo seed requires an empty advertisement/media/period database.')
    admin = User.query.filter_by(role='admin', active=True).first()
    if not admin:
        raise click.ClickException('Create an administrator first with create-admin.')
    year, half = period_key()
    period = Period(year=year, half=half, active=True)
    db.session.add(period)
    db.session.flush()
    specs = [('صبح امروز', 'local_one', 4000), ('شهرآرا', 'local_one', 6000),
             ('پیام خراسان', 'local_two', 5500), ('نسیم خراسان', 'local_two', 4500),
             ('خراسان', 'national', 6000), ('اطلاعات', 'national', 4000)]
    outlets = []
    for name, category, share in specs:
        outlet = Outlet(name=name, active=True)
        db.session.add(outlet)
        db.session.flush()
        outlets.append(outlet)
        db.session.add(OutletTarget(period_id=period.id, outlet_id=outlet.id, category=category, share_bp=share))
    organizations = [Organization(name=name, active=True) for name in (
        'اداره کل راه و شهرسازی خراسان رضوی', 'شهرداری مشهد', 'شرکت آب و فاضلاب خراسان رضوی')]
    db.session.add_all(organizations)
    db.session.flush()
    subject = Subject.query.filter_by(name='مناقصه').first()
    start, _ = period_bounds(year, half)
    volumes = [(0, 10), (0, 180), (0, 50), (1, 60), (2, 120), (3, 180), (4, 300), (5, 100)]
    for index, (outlet_index, volume) in enumerate(volumes):
        allocated = max(start, today() - timedelta(days=7 - index))
        db.session.add(Advertisement(
            ma_reference=1002 + index, organization_id=organizations[index % 3].id,
            outlet_id=outlets[outlet_index].id, period_id=period.id, allocation_date=allocated,
            volume_units=volume * 100, creator_id=admin.id, subject_id=subject.id if subject else None,
            subject_description='مناقصه عمومی خدمات و پروژه‌های عمرانی', letter_number=f'۱۴۰۵/۱۲۰/{index + 1}',
            letter_date=allocated, province='خراسان رضوی', ad_type='دولتی، سفارشی',
            print_status='چاپ شده' if index < 5 else 'منتظر چاپ', approval_date=allocated,
            proposed_date=allocated, final_print_date=allocated if index < 5 else None,
            ad_text='فراخوان مناقصه عمومی؛ متقاضیان برای دریافت اسناد و شرایط شرکت به سازمان آگهی‌دهنده مراجعه کنند.',
            page_number=4, page_type='داخلی', box_size='کادر استاندارد',
            distribution_location='خراسان رضوی', amount=volume * 250000, approved_amount=volume * 250000,
            approved_units=volume * 100, approver_name=admin.name, notes='داده نمایشی برای بررسی امکانات سامانه'))
    db.session.flush()
    db.session.expire(period, ['targets'])
    update_recovery(period)
    db.session.add(Setting(key='demo_mode', value='1'))
    db.session.add(AuditEvent(actor_id=admin.id, action='demo_seed', target=f'period:{period.id}',
                             detail=json.dumps({'boxes': 1000, 'demo': True})))
    db.session.commit()


def register_commands(app):
    @app.cli.command('migrate')
    def migrate_command():
        """Initialize a fresh database, or verify the current schema without changes."""
        click.echo(migrate())

    @app.cli.command('seed-admin')
    def seed_admin():
        """Create or reset the requested admin account and invalidate its old sessions."""
        migrate()
        user = User.query.filter_by(username='admin').first()
        if user is None:
            user = User(username='admin', name='مدیر سامانه', auth_version=0)
            db.session.add(user)
        user.role, user.active = 'admin', True
        user.password_hash = generate_password_hash('123asd#')
        user.auth_version += 1
        db.session.flush()
        db.session.add(AuditEvent(actor_id=user.id, action='admin_seed', target=f'user:{user.id}',
                                 detail=json.dumps({'username': 'admin', 'role': 'admin', 'active': True})))
        db.session.commit()
        click.echo('Admin account ready (username: admin). Previous sessions for this account were invalidated.')

    @app.cli.command('create-admin')
    @click.option('--username', prompt=True)
    @click.option('--name', prompt='Display name')
    @click.password_option()
    def create_admin(username, name, password):
        """Create one personal administrator; never changes an existing password."""
        from .services import text
        username, name = text(username), text(name)
        if not re.fullmatch(r'[\w.@+-]{3,80}', username) or not name or len(name) > 150:
            raise click.ClickException('Provide a 3–80 character username without spaces and a display name.')
        if not 8 <= len(password) <= 200:
            raise click.ClickException('Password must contain 8–200 characters.')
        if User.query.filter_by(username=username).first():
            raise click.ClickException('Username already exists; no account changed.')
        user = User(username=username, name=name, role='admin', active=True, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()
        db.session.add(AuditEvent(actor_id=user.id, action='admin_bootstrap', target=f'user:{user.id}', detail='{}'))
        db.session.commit()
        click.echo('Personal administrator created.')

    @app.cli.command('reset-password')
    @click.option('--username', prompt=True)
    @click.password_option()
    def reset_password(username, password):
        """Operator recovery command; invalidate all existing sessions for this account."""
        from .services import text
        user = User.query.filter_by(username=text(username)).first()
        if not user or not 8 <= len(password) <= 200:
            raise click.ClickException('Account not found or password length invalid; no changes made.')
        user.password_hash = generate_password_hash(password)
        user.auth_version += 1
        db.session.add(AuditEvent(action='password_reset_cli', target=f'user:{user.id}', detail='{}'))
        db.session.commit()
        click.echo('Password reset; existing sessions invalidated.')

    @app.cli.command('backup')
    @click.argument('destination', type=click.Path())
    def backup_command(destination):
        """Write a consistent SQLite snapshot plus immutable advertisement files to ZIP."""
        click.echo(str(backup(destination)))

    @app.cli.command('demo-data')
    def demo_data():
        """Explicitly seed a fresh instance with illustrative 1,000-box allocations."""
        seed_demo()
        click.echo('Created demonstration data. All volumes and media shares remain configurable.')

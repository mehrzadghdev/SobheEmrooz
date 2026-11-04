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
from sqlalchemy.schema import CreateTable, CreateIndex
from werkzeug.security import generate_password_hash

from .allocations import update_recovery
from .models import (Advertisement, AuditEvent, Organization, Outlet, Period,
                     SchemaVersion, Setting, Subject, User, db)
from .membership import enroll_period
from .services import period_bounds, period_key, today


def migrate():
    from .directive import DirectiveRecord
    tables = inspect(db.engine).get_table_names()
    if 'schema_version' in tables:
        versions = [row.version for row in SchemaVersion.query.order_by(SchemaVersion.version)]
        if versions == [1, 2, 3, 4, 5]:
            db.session.rollback()
            with db.engine.begin() as connection:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
                DirectiveRecord.__table__.create(connection, checkfirst=True)
                connection.exec_driver_sql('INSERT INTO schema_version (version, applied_at) VALUES (6, CURRENT_TIMESTAMP)')
            return 'Upgraded schema version 5 to version 6. Historical records preserved; directive policy awaits activation.'
        if versions == [1, 2, 3, 4, 5, 6]:
            return 'Schema version 6 is already current.'
    if tables:
        if 'schema_version' not in tables:
            raise click.ClickException('Unversioned database; refusing to alter it. Use a new SOBHE_DATA_DIR.')
        versions = [row.version for row in SchemaVersion.query.order_by(SchemaVersion.version)]
        if versions == [1, 2, 3, 4, 5]:
            return 'Schema version 5 is already current.'
        if versions not in ([1], [1, 2], [1, 2, 3], [1, 2, 3, 4]):
            raise click.ClickException('Unsupported schema version; no changes made.')
        db.session.rollback()
        # SQLite requires foreign keys disabled outside the transaction to rebuild
        # a referenced table. Validate every reference before committing and always
        # restore enforcement, including when any migration statement fails.
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
            try:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
                missing = connection.exec_driver_sql('''SELECT COUNT(*) FROM advertisement a
                    WHERE NOT EXISTS (SELECT 1 FROM outlet_target t
                        WHERE t.period_id = a.period_id AND t.outlet_id = a.outlet_id)''').scalar_one()
                if missing:
                    raise click.ClickException('Historical advertisements lack period categories; repair the legacy plan before migration.')
                if versions != [1, 2, 3, 4]:
                    if versions != [1, 2, 3]:
                        _upgrade_media_categories(connection, versions)
                    _upgrade_yearly_periods(connection)
                    connection.exec_driver_sql('INSERT INTO schema_version (version, applied_at) VALUES (4, CURRENT_TIMESTAMP)')
                _upgrade_optional_volume(connection)
                connection.exec_driver_sql('INSERT INTO schema_version (version, applied_at) VALUES (5, CURRENT_TIMESTAMP)')
                DirectiveRecord.__table__.create(connection, checkfirst=True)
                connection.exec_driver_sql('INSERT INTO schema_version (version, applied_at) VALUES (6, CURRENT_TIMESTAMP)')
                if connection.exec_driver_sql('PRAGMA foreign_key_check').all():
                    raise click.ClickException('Foreign key validation failed; no changes made.')
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        db.session.expire_all()
        pending = Outlet.query.filter(Outlet.category.is_(None)).count()
        return f'Upgraded to schema version 5 and version 6. Advertisement volume is optional; annual 50% local / 50% national targets are unchanged. Media awaiting type selection: {pending}.'
    db.create_all()
    for version in (1, 2, 3, 4, 5, 6):
        db.session.add(SchemaVersion(version=version))
    for name in ('مناقصه', 'مزایده', 'ثبت شرکت', 'ابلاغیه قضایی', 'اطلاعیه'):
        db.session.add(Subject(name=name, active=True))
    db.session.commit()
    with db.engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    return 'Initialized schema version 6. Create your personal administrator with create-admin.'


def _upgrade_optional_volume(connection):
    table = Advertisement.__table__
    ddl = str(CreateTable(table).compile(db.engine))
    ddl = ddl.replace('CREATE TABLE advertisement (', 'CREATE TABLE advertisement_optional (', 1)
    connection.exec_driver_sql(ddl)
    quote = db.engine.dialect.identifier_preparer.quote
    columns = ', '.join(quote(column.name) for column in table.columns)
    connection.exec_driver_sql(f'INSERT INTO advertisement_optional ({columns}) SELECT {columns} FROM advertisement')
    connection.exec_driver_sql('DROP TABLE advertisement')
    connection.exec_driver_sql('ALTER TABLE advertisement_optional RENAME TO advertisement')
    for index in table.indexes:
        connection.execute(CreateIndex(index))


def _upgrade_media_categories(connection, versions):
    if versions == [1]:
        connection.exec_driver_sql("ALTER TABLE outlet ADD COLUMN category VARCHAR(20) "
                                   "CHECK (category IN ('local_one','local_two','national'))")
        connection.exec_driver_sql('''UPDATE outlet SET category = (
            SELECT t.category FROM outlet_target t JOIN period p ON p.id = t.period_id
            WHERE t.outlet_id = outlet.id ORDER BY p.year DESC, p.half DESC LIMIT 1)''')
        connection.exec_driver_sql('''INSERT INTO outlet_target (period_id, outlet_id, category, share_bp)
            SELECT p.id, o.id, o.category, 0 FROM period p CROSS JOIN outlet o
            WHERE o.active = 1 AND o.category IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM outlet_target t WHERE t.period_id = p.id AND t.outlet_id = o.id)''')
        connection.exec_driver_sql('INSERT INTO schema_version (version, applied_at) VALUES (2, CURRENT_TIMESTAMP)')
    connection.exec_driver_sql('''CREATE TABLE outlet_merged (
        id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(150) NOT NULL UNIQUE,
        active BOOLEAN NOT NULL, category VARCHAR(20),
        CHECK (category IN ('local','national')))''')
    connection.exec_driver_sql('''INSERT INTO outlet_merged SELECT id, name, active,
        CASE WHEN category IN ('local_one','local_two') THEN 'local' ELSE category END FROM outlet''')
    connection.exec_driver_sql('''CREATE TABLE outlet_target_merged (
        id INTEGER NOT NULL PRIMARY KEY, period_id INTEGER NOT NULL REFERENCES period(id),
        outlet_id INTEGER NOT NULL REFERENCES outlet(id), category VARCHAR(20) NOT NULL,
        share_bp INTEGER NOT NULL, UNIQUE (period_id, outlet_id),
        CHECK (category IN ('local','national')), CHECK (share_bp BETWEEN 0 AND 10000))''')
    connection.exec_driver_sql('''INSERT INTO outlet_target_merged SELECT id, period_id, outlet_id,
        CASE WHEN category IN ('local_one','local_two') THEN 'local' ELSE category END,
        share_bp FROM outlet_target''')
    connection.exec_driver_sql('DROP TABLE outlet_target')
    connection.exec_driver_sql('DROP TABLE outlet')
    connection.exec_driver_sql('ALTER TABLE outlet_merged RENAME TO outlet')
    connection.exec_driver_sql('ALTER TABLE outlet_target_merged RENAME TO outlet_target')
    connection.exec_driver_sql('CREATE INDEX ix_outlet_target_period_id ON outlet_target (period_id)')
    connection.exec_driver_sql("DELETE FROM setting WHERE key IN ('label_local_one','label_local_two')")
    connection.exec_driver_sql('INSERT INTO schema_version (version, applied_at) VALUES (3, CURRENT_TIMESTAMP)')


def _upgrade_yearly_periods(connection):
    periods = [dict(row) for row in connection.exec_driver_sql(
        'SELECT * FROM period ORDER BY year, half, id').mappings()]
    targets = [dict(row) for row in connection.exec_driver_sql(
        'SELECT * FROM outlet_target ORDER BY id').mappings()]
    used = {tuple(row) for row in connection.exec_driver_sql(
        'SELECT DISTINCT period_id, outlet_id FROM advertisement')}
    volumes = {tuple(row[:2]): row[2] for row in connection.exec_driver_sql(
        'SELECT period_id, outlet_id, SUM(volume_units) FROM advertisement WHERE voided_at IS NULL GROUP BY period_id, outlet_id')}
    connection.exec_driver_sql('''CREATE TABLE period_yearly (
        id INTEGER NOT NULL PRIMARY KEY, year INTEGER NOT NULL UNIQUE,
        active BOOLEAN NOT NULL, recovery BOOLEAN NOT NULL, revision INTEGER NOT NULL,
        CHECK (year BETWEEN 1200 AND 1600))''')
    connection.exec_driver_sql('''CREATE TABLE outlet_target_yearly (
        id INTEGER NOT NULL PRIMARY KEY, period_id INTEGER NOT NULL REFERENCES period(id),
        outlet_id INTEGER NOT NULL REFERENCES outlet(id), category VARCHAR(20) NOT NULL,
        share_bp INTEGER NOT NULL, UNIQUE (period_id, outlet_id),
        CHECK (category IN ('local','national')), CHECK (share_bp BETWEEN 0 AND 10000))''')
    mapping = {}
    for year in sorted({period['year'] for period in periods}):
        halves = [period for period in periods if period['year'] == year]
        retained_id = min(period['id'] for period in halves)
        order = {period['id']: index for index, period in enumerate(halves)}
        members = [target for target in targets if target['period_id'] in order]
        local, total = 0, 0
        for outlet_id in sorted({target['outlet_id'] for target in members}):
            entries = sorted((target for target in members if target['outlet_id'] == outlet_id),
                             key=lambda target: order[target['period_id']])
            historical = [target for target in entries if (target['period_id'], outlet_id) in used]
            if len({target['category'] for target in historical}) > 1:
                raise click.ClickException(
                    f'Cannot merge year {year}: media {outlet_id} has recorded allocations in both local and national categories. '
                    'Resolve this historical classification conflict before retrying; no changes made.')
            chosen = (historical or entries)[-1]
            amount = sum(volumes.get((target['period_id'], outlet_id), 0) for target in entries)
            total += amount
            if chosen['category'] == 'local':
                local += amount
            connection.exec_driver_sql(
                'INSERT INTO outlet_target_yearly (id,period_id,outlet_id,category,share_bp) VALUES (?,?,?,?,?)',
                (min(target['id'] for target in entries), retained_id, outlet_id, chosen['category'], chosen['share_bp']))
        recovery = 5 * local > 3 * total or (2 * local > total and any(period['recovery'] for period in halves))
        connection.exec_driver_sql(
            'INSERT INTO period_yearly (id,year,active,recovery,revision) VALUES (?,?,?,?,?)',
            (retained_id, year, any(period['active'] for period in halves), recovery,
             max(period['revision'] for period in halves) + 1))
        for period in halves:
            mapping[str(period['id'])] = retained_id
            connection.exec_driver_sql('UPDATE advertisement SET period_id = ? WHERE period_id = ?',
                                       (retained_id, period['id']))
    connection.exec_driver_sql('DROP TABLE outlet_target')
    connection.exec_driver_sql('DROP TABLE period')
    connection.exec_driver_sql('ALTER TABLE period_yearly RENAME TO period')
    connection.exec_driver_sql('ALTER TABLE outlet_target_yearly RENAME TO outlet_target')
    connection.exec_driver_sql('CREATE INDEX ix_outlet_target_period_id ON outlet_target (period_id)')
    connection.exec_driver_sql(
        'INSERT INTO audit_event (action,target,detail,created_at) VALUES (?,?,?,CURRENT_TIMESTAMP)',
        ('periods_yearly_migration', 'periods', json.dumps(
            {'period_mapping': mapping, 'previous_periods': periods, 'previous_memberships': targets}, ensure_ascii=False)))


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
    year = period_key()
    period = Period(year=year, active=True)
    db.session.add(period)
    db.session.flush()
    specs = [('صبح امروز', 'local'), ('شهرآرا', 'local'),
             ('پیام خراسان', 'local'), ('نسیم خراسان', 'local'),
             ('خراسان', 'national'), ('اطلاعات', 'national')]
    outlets = []
    for name, category in specs:
        outlet = Outlet(name=name, active=True, category=category)
        db.session.add(outlet)
        db.session.flush()
        outlets.append(outlet)
    enroll_period(period)
    organizations = [Organization(name=name, active=True) for name in (
        'اداره کل راه و شهرسازی خراسان رضوی', 'شهرداری مشهد', 'شرکت آب و فاضلاب خراسان رضوی')]
    db.session.add_all(organizations)
    db.session.flush()
    subject = Subject.query.filter_by(name='مناقصه').first()
    start, _ = period_bounds(year)
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
        """Initialize or transactionally upgrade the SQLite schema."""
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
        click.echo('Created demonstration data. Group targets are 50% local / 50% national; media types are editable.')

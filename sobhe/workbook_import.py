"""Transactional import of the supplied 1405 advertisement export."""
import hashlib
import json
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import click
from flask import current_app
from openpyxl import load_workbook
from sqlalchemy import func

from .allocations import update_recovery
from .membership import enroll_period
from .models import Advertisement, AuditEvent, Organization, Outlet, Period, Setting, User, db
from .services import integer, parse_date, period_key

LOCAL_MEDIA = {'خراسان رضوی', 'صبح امروز خراسان رضوی'}
IMPORT_KEY = 'workbook_1405_sheet2'
HEADERS = ('سازمان آگهی دهنده', 'رسانه', 'تاریخ پیشنهادی', 'اندازه کادر', 'مبلغ آگهی')
IDENTIFIED_HEADERS = ('شناسه آگهی', *HEADERS)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_workbook(path, command_name):
    """Resolve bundled defaults and macOS/Linux Unicode filename differences."""
    if path is None:
        filename = '12 آبان.xlsx' if command_name == 'import-12-aban' else 'آگهی 1405.xlsx'
        path = PROJECT_ROOT / filename
    path = Path(path).expanduser()
    if path.is_file():
        return path
    if path.parent.is_dir():
        normalized = unicodedata.normalize('NFC', path.name)
        matches = [candidate for candidate in path.parent.iterdir()
                   if candidate.is_file() and unicodedata.normalize('NFC', candidate.name) == normalized]
        if len(matches) == 1:
            return matches[0]
    raise ValueError(f'Workbook not found in deployment: {path}')


def read_rows(path):
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook['Sheet2'] if 'Sheet2' in workbook.sheetnames else workbook['Sheet1']
        headers = tuple(next(sheet.values, ()))
        identified = headers[:6] == IDENTIFIED_HEADERS
        if not identified and headers[:5] != HEADERS:
            raise ValueError(f'Unexpected {sheet.title} headers.')
        rows = []
        for number, values in enumerate(sheet.iter_rows(min_row=2, max_col=6 if identified else 5, values_only=True), 2):
            if not any(value is not None for value in values):
                continue
            if values[0] == 'لازم است به منظور محدود کردن پاسخ جستجو، از فیلترهای بیشتری استفاده کنید.' and not any(values[1:]):
                continue
            try:
                source_id = integer(values[0], 1) if identified else None
                org, outlet, date, volume, amount = values[1:] if identified else values
                if not isinstance(org, str) or not org.strip() or not isinstance(outlet, str) or not outlet.strip():
                    raise ValueError('Missing organization/media')
                allocated = parse_date(str(date))
                boxes = Decimal(str(volume)) * 100
                money = Decimal(str(amount))
                if not boxes.is_finite() or boxes <= 0 or boxes != boxes.to_integral_value():
                    raise ValueError('Invalid volume')
                if not money.is_finite() or money < 0 or money != money.to_integral_value():
                    raise ValueError('Invalid amount')
                if period_key(allocated) != 1405:
                    raise ValueError('Expected year 1405')
                rows.append(dict(row=number, organization=org.strip(), outlet=outlet.strip(),
                                 date=allocated.isoformat(), volume=int(boxes), amount=int(money)))
                if identified:
                    rows[-1]['source_id'] = source_id
            except (ValueError, TypeError, ArithmeticError) as error:
                raise ValueError(f'{sheet.title} row {number}: {error}') from error
        if not rows:
            raise ValueError(f'{sheet.title} contains no advertisements.')
        return rows
    finally:
        workbook.close()


def row_fingerprint(row):
    """Legacy exports have no source ID; match the publication's five source fields."""
    values = [row[key] for key in ('organization', 'outlet', 'date', 'volume', 'amount')]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def pending_rows(rows, period):
    known = Counter()
    if period:
        # Include voided records so a later export cannot resurrect them.
        records = db.session.query(Advertisement, Organization.name, Outlet.name).join(
            Organization, Advertisement.organization_id == Organization.id).join(
            Outlet, Advertisement.outlet_id == Outlet.id).filter(Advertisement.period_id == period.id)
        for ad, organization, outlet in records:
            known[row_fingerprint(dict(organization=organization.strip(), outlet=outlet.strip(),
                                       date=(ad.proposed_date or ad.allocation_date).isoformat(),
                                       volume=ad.volume_units, amount=ad.amount))] += 1
    # Retain source fingerprints across later edits, voids, renames, and reordered exports.
    for event in AuditEvent.query.filter_by(action='workbook_import', target='period:1405'):
        known |= Counter(json.loads(event.detail).get('row_counts', {}))
    pending = []
    for row in rows:
        key = row_fingerprint(row)
        if known[key]:
            known[key] -= 1
        else:
            pending.append(row)
    return pending


def import_rows(rows, username, source_name, *, apply=False, incremental=False):
    """Lock, validate, and import; never overwrite existing advertisements."""
    db.session.connection().exec_driver_sql('BEGIN IMMEDIATE')
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    marker = db.session.get(Setting, IMPORT_KEY)
    if marker and not incremental:
        if marker.value != digest:
            raise ValueError('A different workbook was already imported; reconcile it before importing revisions.')
        db.session.rollback()
        return {'already_imported': True, 'advertisements': len(rows)}
    actor = User.query.filter_by(username=username, role='admin', active=True).first()
    if actor is None:
        raise ValueError('An existing active administrator username is required.')
    period = Period.query.filter_by(year=1405).first()
    source_rows = rows
    if incremental:
        rows = pending_rows(rows, period)
    elif period and Advertisement.query.filter_by(period_id=period.id).first():
        raise ValueError('1405 already has advertisements; reconcile existing history before importing.')
    counts = Counter(outlet=0, organization=0, advertisement=0)
    names = sorted({row['outlet'] for row in rows})
    outlets = {}
    for name in names:
        category = 'local' if name in LOCAL_MEDIA else 'national'
        outlet = Outlet.query.filter_by(name=name).first()
        if outlet and (not outlet.active or outlet.category != category):
            raise ValueError(f'Existing media classification/activity conflicts: {name}')
        if outlet is None:
            outlet = Outlet(name=name, category=category, active=True)
            db.session.add(outlet)
            counts['outlet'] += 1
        outlets[name] = outlet
    organizations = {}
    for name in sorted({row['organization'] for row in rows}):
        organization = Organization.query.filter_by(name=name).first()
        if organization and not organization.active:
            raise ValueError(f'Existing organization is archived: {name}')
        if organization is None:
            organization = Organization(name=name, active=True)
            db.session.add(organization)
            counts['organization'] += 1
        organizations[name] = organization
    if period is None:
        period = Period(year=1405, active=True)
        db.session.add(period)
        db.session.flush()
        enroll_period(period)
    else:
        db.session.flush()
        from .membership import sync_outlet
        for outlet in outlets.values():
            sync_outlet(outlet)
    db.session.flush()
    first_ma = max(9000000000000, (db.session.query(func.max(Advertisement.ma_reference)).scalar() or 0) + 1)
    for index, row in enumerate(rows):
        allocated = parse_date(row['date'])
        source = (f'Sheet1، ردیف {row["row"]}، شناسه آگهی در منبع: {row["source_id"]}'
                  if 'source_id' in row else f'Sheet2، ردیف {row["row"]}')
        db.session.add(Advertisement(
            ma_reference=first_ma + index, organization_id=organizations[row['organization']].id,
            outlet_id=outlets[row['outlet']].id, period_id=period.id, allocation_date=allocated,
            proposed_date=allocated, volume_units=row['volume'], amount=row['amount'], creator_id=actor.id,
            notes=f'ورود از {source_name}، {source}. شناسه آگهی تولیدشده؛ تاریخ تخصیص بر اساس تاریخ پیشنهادی؛ وضعیت چاپ در منبع مشخص نیست.'))
        counts['advertisement'] += 1
    db.session.flush()
    db.session.expire(period, ['targets'])
    if rows:
        update_recovery(period)
    summary = dict(counts, boxes=sum(row['volume'] for row in rows) / 100,
                   amount=sum(row['amount'] for row in rows), first_ad_id=first_ma if rows else None,
                   last_ad_id=first_ma + len(rows) - 1 if rows else None, source_sha256=digest)
    if incremental:
        summary.update(source_rows=len(source_rows), skipped=len(source_rows) - len(rows),
                       already_imported=not rows)
    source_ids = [row['source_id'] for row in source_rows if 'source_id' in row]
    if source_ids:
        summary.update(source_ids=len(set(source_ids)), repeated_source_rows=len(source_ids) - len(set(source_ids)))
    if not marker and not incremental:
        db.session.add(Setting(key=IMPORT_KEY, value=digest))
    audit_summary = dict(summary, row_counts=dict(Counter(row_fingerprint(row) for row in source_rows)))
    db.session.add(AuditEvent(actor_id=actor.id, action='workbook_import', target='period:1405',
                             detail=json.dumps(audit_summary)))
    if apply:
        db.session.commit()
    else:
        db.session.rollback()
    return summary


def register_import_command(app):
    @app.cli.command('import-1405')
    @click.argument('workbook', required=False, type=click.Path(dir_okay=False, path_type=Path))
    @click.option('--username', required=True, help='Existing active administrator to attribute the import to.')
    @click.option('--apply', is_flag=True, help='Write records; default is a dry run.')
    @click.option('--incremental', is_flag=True, help='Skip existing publication rows and import only new rows.')
    def command(workbook, username, apply, incremental):
        """Import a 1405 workbook with approved media mapping and generated advertisement IDs."""
        try:
            workbook = resolve_workbook(workbook, click.get_current_context().info_name)
            rows = read_rows(workbook)
            if apply:
                from .commands import backup
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                destination = Path(current_app.instance_path) / 'backups' / f'before-import-1405-{stamp}.zip'
                destination.parent.mkdir(parents=True, exist_ok=True)
                click.echo(f'Backup: {backup(destination)}')
            incremental = incremental or click.get_current_context().info_name == 'import-12-aban'
            result = import_rows(rows, username, workbook.name, apply=apply, incremental=incremental)
            click.echo(('APPLIED: ' if apply else 'DRY RUN: ') + json.dumps(result, ensure_ascii=False))
        except Exception as error:
            db.session.rollback()
            raise click.ClickException(str(error)) from error

    app.cli.add_command(command, 'import-12-aban')

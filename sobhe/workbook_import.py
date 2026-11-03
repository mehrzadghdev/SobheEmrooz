"""Transactional import of the supplied 1405 advertisement export."""
import hashlib
import json
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
from .services import parse_date, period_key

LOCAL_MEDIA = {'خراسان رضوی', 'صبح امروز خراسان رضوی'}
IMPORT_KEY = 'workbook_1405_sheet2'
HEADERS = ('سازمان آگهی دهنده', 'رسانه', 'تاریخ پیشنهادی', 'اندازه کادر', 'مبلغ آگهی')


def read_rows(path):
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook['Sheet2']
        if tuple(next(sheet.values)[:5]) != HEADERS:
            raise ValueError('Unexpected Sheet2 headers.')
        rows = []
        for number, values in enumerate(sheet.iter_rows(min_row=2, max_col=5, values_only=True), 2):
            if not any(value is not None for value in values):
                continue
            if values[0] == 'لازم است به منظور محدود کردن پاسخ جستجو، از فیلترهای بیشتری استفاده کنید.' and not any(values[1:]):
                continue
            org, outlet, date, volume, amount = values
            try:
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
            except (ValueError, TypeError, ArithmeticError) as error:
                raise ValueError(f'Sheet2 row {number}: {error}') from error
        if not rows:
            raise ValueError('Sheet2 contains no advertisements.')
        return rows
    finally:
        workbook.close()


def import_rows(rows, username, source_name, *, apply=False):
    """Lock, validate, and import; never overwrite existing advertisements."""
    db.session.connection().exec_driver_sql('BEGIN IMMEDIATE')
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    marker = db.session.get(Setting, IMPORT_KEY)
    if marker:
        if marker.value != digest:
            raise ValueError('A different Sheet2 was already imported; reconcile it before importing revisions.')
        db.session.rollback()
        return {'already_imported': True, 'advertisements': len(rows)}
    actor = User.query.filter_by(username=username, role='admin', active=True).first()
    if actor is None:
        raise ValueError('An existing active administrator username is required.')
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
    # Existing annual history requires reconciliation instead of a blind bulk merge.
    period = Period.query.filter_by(year=1405).first()
    if period and Advertisement.query.filter_by(period_id=period.id).first():
        raise ValueError('1405 already has advertisements; reconcile existing history before importing.')
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
        db.session.add(Advertisement(
            ma_reference=first_ma + index, organization_id=organizations[row['organization']].id,
            outlet_id=outlets[row['outlet']].id, period_id=period.id, allocation_date=allocated,
            proposed_date=allocated, volume_units=row['volume'], amount=row['amount'], creator_id=actor.id,
            notes=f'ورود از {source_name}، Sheet2، ردیف {row["row"]}. شناسه م.ا. تولیدشده؛ تاریخ تخصیص بر اساس تاریخ پیشنهادی؛ وضعیت چاپ در منبع مشخص نیست.'))
        counts['advertisement'] += 1
    db.session.flush()
    db.session.expire(period, ['targets'])
    update_recovery(period)
    summary = dict(counts, boxes=sum(row['volume'] for row in rows) / 100,
                   amount=sum(row['amount'] for row in rows), first_ma=first_ma,
                   last_ma=first_ma + len(rows) - 1, source_sha256=digest)
    db.session.add(Setting(key=IMPORT_KEY, value=digest))
    db.session.add(AuditEvent(actor_id=actor.id, action='workbook_import', target='period:1405',
                             detail=json.dumps(summary)))
    if apply:
        db.session.commit()
    else:
        db.session.rollback()
    return summary


def register_import_command(app):
    @app.cli.command('import-1405')
    @click.argument('workbook', type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option('--username', required=True, help='Existing active administrator to attribute the import to.')
    @click.option('--apply', is_flag=True, help='Write records; default is a dry run.')
    def command(workbook, username, apply):
        """Import Sheet2 with the approved 1405 media mapping and generated M.A. IDs."""
        try:
            rows = read_rows(workbook)
            if apply:
                from .commands import backup
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                destination = Path(current_app.instance_path) / 'backups' / f'before-import-1405-{stamp}.zip'
                destination.parent.mkdir(parents=True, exist_ok=True)
                click.echo(f'Backup: {backup(destination)}')
            result = import_rows(rows, username, workbook.name, apply=apply)
            click.echo(('APPLIED: ' if apply else 'DRY RUN: ') + json.dumps(result, ensure_ascii=False))
        except Exception as error:
            db.session.rollback()
            raise click.ClickException(str(error)) from error

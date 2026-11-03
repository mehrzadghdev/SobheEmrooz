"""Public report filters and allowlisted display/export columns."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import jdatetime
from sqlalchemy import func
from .constants import AD_TYPES, COLUMNS, DATE_FIELDS, DEFAULT_COLUMNS, PRINT_STATUSES, PROVINCES
from .models import Advertisement, OutletTarget, db
from .services import boxes, category_labels, integer, jalali, parse_date, period_bounds, text


def selected_columns(args):
    values = args.getlist('columns') if 'columns' in args or args.get('column_selection') == '1' else list(DEFAULT_COLUMNS)
    if not values or any(value not in COLUMNS for value in values):
        raise ValueError('حداقل یک ستون معتبر برای گزارش انتخاب کنید.')
    return [key for key in COLUMNS if key in values]


def filtered_query(args, year, *, apply_dates=True):
    start_default, end_default = period_bounds(year)
    start = parse_date(args.get('start') or start_default.isoformat())
    end = parse_date(args.get('end') or (end_default - timedelta(days=1)).isoformat())
    if start > end:
        raise ValueError('ابتدای بازه باید پیش از انتهای بازه باشد.')
    jstart = jdatetime.date.fromgregorian(date=start)
    try:
        anniversary = jdatetime.date(jstart.year + 1, jstart.month, jstart.day).togregorian()
    except ValueError:
        anniversary = jdatetime.date(jstart.year + 1, jstart.month, 29).togregorian()
    if end >= anniversary:
        raise ValueError('حداکثر بازه تاریخ گزارش یک سال شمسی است.')
    field = args.get('date_field', 'approval_date')
    if field not in DATE_FIELDS:
        raise ValueError('نوع تاریخ گزارش معتبر نیست.')
    column = getattr(Advertisement, field)
    query = Advertisement.query.filter(Advertisement.voided_at.is_(None))
    if apply_dates or args.get('start') or args.get('end'):
        if field == 'created_at':
            zone = ZoneInfo('Asia/Tehran')
            lower = datetime.combine(start, datetime.min.time(), zone).astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
            upper = datetime.combine(end + timedelta(days=1), datetime.min.time(), zone).astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
            query = query.filter(column >= lower, column < upper)
        else:
            query = query.filter(column >= start, column <= end)
    for field_name, minimum_key, maximum_key in (
        ('id', 'id_min', 'id_max'), ('ma_reference', 'ma_min', 'ma_max'),
        ('amount', 'amount_min', 'amount_max'), ('approved_amount', 'approved_amount_min', 'approved_amount_max')):
        lower = integer(args.get(minimum_key), optional=True)
        upper = integer(args.get(maximum_key), optional=True)
        if lower is not None and upper is not None and lower > upper:
            raise ValueError('حداقل عدد نمی‌تواند بیشتر از حداکثر باشد.')
        if lower is not None:
            query = query.filter(getattr(Advertisement, field_name) >= lower)
        if upper is not None:
            query = query.filter(getattr(Advertisement, field_name) <= upper)
    for key in ('organization_id', 'outlet_id', 'subject_id'):
        value = integer(args.get(key), 1, optional=True)
        if value:
            query = query.filter(getattr(Advertisement, key) == value)
    if args.get('province'):
        if args['province'] not in PROVINCES:
            raise ValueError('استان معتبر انتخاب کنید.')
        query = query.filter_by(province=args['province'])
    for key in ('letter_number', 'subject_description', 'ad_text'):
        value = text(args.get(key))
        if len(value) > 500:
            raise ValueError('عبارت جستجو بیش از حد طولانی است.')
        if value:
            query = query.filter(getattr(Advertisement, key).contains(value, autoescape=True))
    for key, allowed in [('ad_type', AD_TYPES), ('print_status', PRINT_STATUSES)]:
        values = args.getlist(key)
        if any(value not in allowed for value in values):
            raise ValueError('نوع آگهی یا وضعیت چاپ معتبر نیست.')
        if values:
            query = query.filter(getattr(Advertisement, key).in_(values))
    return query, {'start': start, 'end': end, 'date_field': field}


def cell(ad, key):
    if key == 'organization':
        return ad.organization.name
    if key == 'outlet':
        return ad.outlet.name
    if key == 'subject':
        return ad.subject.name if ad.subject else '—'
    if key == 'file':
        return ad.file_name or '—'
    value = getattr(ad, key)
    if key in DATE_FIELDS:
        return jalali(value)
    if key.endswith('_units'):
        return boxes(value) if value is not None else '—'
    return value if value not in (None, '') else '—'


PERSIAN_MONTHS = ('فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
                  'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند')


def allocation_charts(state, year):
    """Annual allocation charts use recorded period categories and nonvoid volume."""
    months = [{'name': name, 'local': 0, 'national': 0} for name in PERSIAN_MONTHS]
    if state['period']:
        daily = db.session.query(Advertisement.allocation_date, OutletTarget.category,
                                 func.sum(Advertisement.volume_units)).join(OutletTarget,
            (OutletTarget.period_id == Advertisement.period_id)
            & (OutletTarget.outlet_id == Advertisement.outlet_id)).filter(
                Advertisement.period_id == state['period'].id, Advertisement.voided_at.is_(None)
            ).group_by(Advertisement.allocation_date, OutletTarget.category)
        for allocated, category, volume in daily:
            month = jdatetime.date.fromgregorian(date=allocated).month
            months[month - 1][category] += int(volume or 0)
    labels = category_labels()
    groups = []
    for key, group in state['groups'].items():
        media = [{'id': row['outlet_id'], 'name': row['name'], 'units': row['allocated_units'],
                  'allocated': row['allocated'], 'percent': row['category_percent']}
                 for row in state['rows'] if row['category'] == key]
        media.sort(key=lambda row: (-row['units'], row['id']))
        groups.append({'key': key, 'label': labels[key], 'units': group['allocated_units'],
                       'allocated': group['allocated'], 'percent': group['actual_percent'], 'media': media})
    return {'year': year, 'total': state['total'], 'total_units': state['total_units'],
            'groups': groups, 'months': months}

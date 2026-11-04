"""Public report filters and allowlisted display/export columns."""
from datetime import datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

import jdatetime
from sqlalchemy import func
from .constants import AD_TYPES, COLUMNS, DATE_FIELDS, DEFAULT_COLUMNS, PRINT_STATUSES, PROVINCES
from .models import Advertisement, db
from .services import boxes, category_labels, decimal_string, integer, jalali, parse_date, period_bounds, text, today


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
    # Every record has an allocation date; approval and print dates are optional.
    field = args.get('date_field', 'allocation_date')
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
    if key == 'official_reference':
        from .directive import official_reference
        return official_reference(ad)
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


def chart_args(args, kind):
    """Each chart owns its range while retaining the other chart's URL state."""
    return {f'chart_{key}': args[f'chart_{kind}_{key}'] for key in ('range', 'month', 'date')
            if f'chart_{kind}_{key}' in args}


def chart_window(args, year):
    """Chart scopes stay within the selected Persian year; weeks start Saturday."""
    start, end = period_bounds(year)
    anchor = min(max(today(), start), end - timedelta(days=1))
    mode = args.get('chart_range', 'yearly')
    if mode not in ('yearly', 'monthly', 'weekly'):
        raise ValueError('بازه نمودار معتبر نیست.')
    month = jdatetime.date.fromgregorian(date=anchor).month
    if mode == 'monthly':
        month = integer(args.get('chart_month', month), 1, 12)
        lower = jdatetime.date(year, month, 1).togregorian()
        upper = jdatetime.date(year, month + 1, 1).togregorian() if month < 12 else end
        label = f'{PERSIAN_MONTHS[month - 1]} {year}'
    elif mode == 'weekly':
        anchor = parse_date(args.get('chart_date') or anchor.isoformat())
        if not start <= anchor < end:
            raise ValueError('تاریخ هفته باید در سال انتخاب‌شده باشد.')
        saturday = anchor - timedelta(days=(anchor.weekday() + 2) % 7)
        lower, upper = max(start, saturday), min(end, saturday + timedelta(days=7))
        label = f'{jalali(lower)} تا {jalali(upper - timedelta(days=1))}'
    else:
        lower, upper, label = start, end, f'سال {year}'
    return {'mode': mode, 'start': lower.isoformat(), 'end': (upper - timedelta(days=1)).isoformat(),
            'label': label, 'month': month, 'date': anchor.isoformat(),
            'year_start': start.isoformat(), 'year_end': (end - timedelta(days=1)).isoformat()}


def allocation_charts(state, year, args=None):
    """Bar series and pie totals share allocation dates and recorded media categories."""
    from .allocations import group_stats

    window = chart_window(args or {}, year)
    start, end = parse_date(window['start']), parse_date(window['end'])
    if window['mode'] == 'yearly':
        buckets = [{'label': name, 'name': f'{name} {year}', 'local': 0, 'national': 0}
                   for name in PERSIAN_MONTHS]
    else:
        weekdays = ('دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه')
        days = [start + timedelta(days=index) for index in range((end - start).days + 1)]
        buckets = [{'label': weekdays[day.weekday()] if window['mode'] == 'weekly'
                    else str(jdatetime.date.fromgregorian(date=day).day),
                    'name': jalali(day), 'local': 0, 'national': 0} for day in days]
    allocated = {}
    if state['period']:
        categories = {row['outlet_id']: row['category'] for row in state['rows']}
        daily = db.session.query(Advertisement.allocation_date, Advertisement.outlet_id,
                                 func.sum(Advertisement.volume_units)).filter(
            Advertisement.period_id == state['period'].id, Advertisement.voided_at.is_(None),
            Advertisement.allocation_date >= start, Advertisement.allocation_date <= end
        ).group_by(Advertisement.allocation_date, Advertisement.outlet_id)
        for day, outlet_id, volume in daily:
            category = categories.get(outlet_id)
            if category is None:
                continue
            units = int(volume or 0)
            allocated[outlet_id] = allocated.get(outlet_id, 0) + units
            index = jdatetime.date.fromgregorian(date=day).month - 1 if window['mode'] == 'yearly' else (day - start).days
            buckets[index][category] += units
    category_units = {key: sum(int(allocated.get(row['outlet_id']) or 0) for row in state['rows']
                               if row['category'] == key) for key in state['groups']}
    total = sum(category_units.values())
    labels = category_labels()
    groups = []
    for key in state['groups']:
        group = group_stats(key, category_units[key], total)
        media = []
        for row in state['rows']:
            if row['category'] != key:
                continue
            units = int(allocated.get(row['outlet_id']) or 0)
            media.append({'id': row['outlet_id'], 'name': row['name'], 'units': units,
                          'allocated': boxes(units),
                          'percent': decimal_string(Fraction(units * 100, category_units[key]))
                          if category_units[key] else '0'})
        media.sort(key=lambda row: (-row['units'], row['id']))
        groups.append({'key': key, 'label': labels[key], 'units': group['allocated_units'],
                       'allocated': group['allocated'], 'percent': group['actual_percent'],
                       'status_class': group['status_class'], 'status_label': group['status_label'], 'media': media})
    return {'year': year, 'total': boxes(total), 'total_units': total,
            'groups': groups, 'window': window, 'buckets': buckets,
            'month_options': [{'value': index + 1, 'label': name} for index, name in enumerate(PERSIAN_MONTHS)]}

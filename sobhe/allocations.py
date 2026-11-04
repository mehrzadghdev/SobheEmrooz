"""Group-wide arithmetic and deterministic, advisory media recommendations."""
import hashlib
import json
from fractions import Fraction

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import func
from .constants import CATEGORIES, WEIGHTS
from .models import Advertisement, Period, db
from .services import boxes, quota_boxes, decimal_string, period_key, category_labels, units


def find_period(value):
    return Period.query.filter_by(year=period_key(value)).first()


def group_stats(category, amount, total):
    expected = Fraction(total * WEIGHTS[category], 100)
    status = 'empty' if not total else ('below-target' if amount < expected else
                                      'above-target' if amount > expected else 'on-target')
    return {'category': category, 'target_percent': WEIGHTS[category],
            'status': status, 'status_class': 'muted' if not total else 'success' if amount == expected else 'warning',
            'status_label': {'empty': 'بدون تخصیص', 'below-target': 'کمتر از هدف گروه',
                             'above-target': 'بیشتر از هدف گروه', 'on-target': 'مطابق هدف گروه'}[status],
            'shortfall': quota_boxes(max(0, expected - amount)),
            'allocated_units': amount, 'allocated': boxes(amount),
            'actual_percent': decimal_string(Fraction(amount * 100, total)) if total else '0',
            'expected_units': expected, 'expected': quota_boxes(expected),
            'headroom': quota_boxes(max(0, expected - amount)),
            'exceeded': amount > expected, 'excess': quota_boxes(max(0, amount - expected))}


def snapshot(period, *, exclude_id=None):
    targets = list(period.targets) if period else []
    query = db.session.query(Advertisement.outlet_id, func.sum(Advertisement.volume_units),
                             func.max(Advertisement.id)).filter(
        Advertisement.period_id == period.id, Advertisement.voided_at.is_(None)) if period else None
    if query is not None and exclude_id:
        query = query.filter(Advertisement.id != exclude_id)
    allocated = {row[0]: (int(row[1] or 0), row[2]) for row in query.group_by(Advertisement.outlet_id)} if query is not None else {}
    category_units = {key: sum(allocated.get(target.outlet_id, (0, 0))[0] for target in targets if target.category == key)
                      for key in CATEGORIES}
    total = sum(category_units.values())
    result = {'total_units': total, 'total': boxes(total), 'categories': category_units,
              'groups': {key: group_stats(key, category_units[key], total) for key in CATEGORIES},
              'local_units': category_units['local'],
              'rows': [], 'targets': targets, 'period': period}
    for target in targets:
        amount, last = allocated.get(target.outlet_id, (0, 0))
        category_total = category_units[target.category]
        result['rows'].append({
            'outlet_id': target.outlet_id, 'name': target.outlet.name, 'category': target.category,
            'active': target.outlet.active and target.outlet.category in CATEGORIES,
            'allocated_units': amount, 'allocated': boxes(amount), 'last': last,
            'actual_percent': decimal_string(Fraction(amount * 100, total)) if total else '0',
            'category_percent': decimal_string(Fraction(amount * 100, category_total)) if category_total else '0',
        })
    for key, group in result['groups'].items():
        group['media_count'] = sum(row['category'] == key for row in result['rows'])
        group['active_count'] = sum(row['category'] == key and row['active'] for row in result['rows'])
    result['local_percent'] = decimal_string(Fraction(result['local_units'] * 100, total)) if total else '0'
    result['national_percent'] = decimal_string(Fraction(category_units['national'] * 100, total)) if total else '0'
    return result


def recommend(state, volume_units):
    total, local = state['total_units'], state['local_units']
    period = state['period']
    recovery = bool(period and period.recovery)
    if not total or 2 * local <= total:
        recovery = False
    elif 5 * local > 3 * total:
        recovery = True
    if total and not recovery and 5 * (local + volume_units) > 3 * (total + volume_units):
        recovery = True
    # Ad size takes priority over percentage targets and recovery mode.
    categories = ['national'] if volume_units < units(5) else ['local']
    eligible = [row for row in state['rows'] if row['active']]
    for category in categories:
        candidates = [row for row in eligible if row['category'] == category]
        if candidates:
            chosen = min(candidates, key=lambda row: (row['allocated_units'], row['last'] or 0, row['outlet_id']))
            return chosen, recovery
    return None, recovery


def public_row(row):
    return {key: value for key, value in row.items() if key not in ('expected_units', 'last')}


def preview(period, volume_units, selected_id=None, *, exclude_id=None, record_version=None):
    if not period or not period.active:
        raise ValueError('ابتدا مدیر باید این دوره را برای ثبت آگهی فعال کند.')
    state = snapshot(period, exclude_id=exclude_id)
    recommended, recovery = recommend(state, volume_units)
    selected_id = selected_id or (recommended['outlet_id'] if recommended else None)
    existing = db.session.get(Advertisement, exclude_id) if exclude_id else None
    retained_id = existing.outlet_id if existing and existing.period_id == period.id else None
    selected = next((row for row in state['rows'] if row['outlet_id'] == selected_id
                     and (row['active'] or row['outlet_id'] == retained_id)), None)
    if selected_id and selected is None:
        raise ValueError('رسانه انتخاب‌شده فعال نیست یا در این دوره وجود ندارد.')
    labels = category_labels()
    warnings = [f'گروه {labels[key]} رسانه فعال ندارد؛ سهم هدف این گروه بین گروه‌های دیگر تقسیم نمی‌شود.'
                for key, group in state['groups'].items() if not group['active_count']]
    if not recommended:
        warnings.append('در گروه موردنیاز رسانه فعال وجود ندارد؛ نوع و وضعیت رسانه‌ها را بررسی کنید.')
    if selected and recommended and selected_id != recommended['outlet_id']:
        warnings.append('رسانه انتخاب‌شده با پیشنهاد سامانه متفاوت است؛ تخصیص دستی ثبت خواهد شد.')
    total_after = state['total_units'] + (volume_units if selected else 0)
    groups = {}
    for key, group in state['groups'].items():
        after = group['allocated_units'] + (volume_units if selected and selected['category'] == key else 0)
        projected = group_stats(key, after, total_after)
        groups[key] = public_row(group)
        groups[key].update({f'projected_{name}': value for name, value in public_row(projected).items()
                            if name not in ('category', 'target_percent')})
    selected_data = public_row(selected) if selected else None
    if selected:
        group = groups[selected['category']]
        after = selected['allocated_units'] + volume_units
        if group['exceeded']:
            warnings.append(f'گروه {labels[selected["category"]]} در حال حاضر از سهم هدف دوره عبور کرده است.')
        if group['projected_exceeded']:
            warnings.append(f'با این تخصیص، گروه {labels[selected["category"]]} از سهم هدف عبور می‌کند: '
                            f'{group["projected_allocated"]} کادر در برابر هدف {group["projected_expected"]} کادر.')
        selected_data.update(projected_allocated=boxes(after),
                             projected_percent=decimal_string(Fraction(after * 100, total_after)) if total_after else '0',
                             projected_category_percent=decimal_string(Fraction(after * 100, group['projected_allocated_units']))
                             if group['projected_allocated_units'] else '0')
    local_after = state['local_units'] + (volume_units if selected and selected['category'] != 'national' else 0)
    if selected and 5 * local_after > 3 * total_after:
        warnings.append('سهم محلی پس از تخصیص از سقف ترجیحی ۶۰ درصد عبور می‌کند؛ پیشنهاد رسانه همچنان بر اساس حجم آگهی است.')
    fingerprint = {
        'period': period.id, 'revision': period.revision, 'volume_units': volume_units,
        'selected': selected_id, 'excluded': exclude_id, 'record_version': record_version,
        'total': state['total_units'], 'local': state['local_units'], 'recovery': period.recovery,
        'recommended': recommended['outlet_id'] if recommended else None,
        'rows': [(r['outlet_id'], r['category'], r['active'], r['allocated_units'], r['last']) for r in state['rows']],
        'warnings': warnings,
    }
    digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    token = URLSafeTimedSerializer(current_app.secret_key, salt='allocation-preview').dumps(digest)
    return {'period_id': period.id, 'period_year': period.year,
            'total': state['total'], 'local_percent': state['local_percent'], 'groups': groups,
            'projected_local_percent': decimal_string(Fraction(local_after * 100, total_after)) if total_after else '0',
            'recovery': recovery, 'recommended': public_row(recommended) if recommended else None,
            'selected': selected_data, 'warnings': warnings, 'token': token, 'fingerprint': digest,
            'options': [{'id': row['outlet_id'], 'name': row['name'], 'category': row['category']} for row in state['rows']
                        if row['active'] or row['outlet_id'] == retained_id]}


def token_matches(token, expected):
    try:
        value = URLSafeTimedSerializer(current_app.secret_key, salt='allocation-preview').loads(token or '', max_age=1800)
        return value == expected['fingerprint']
    except (BadSignature, SignatureExpired):
        return False


def update_recovery(period, requested_mode=None):
    state = snapshot(period)
    if not state['total_units'] or 2 * state['local_units'] <= state['total_units']:
        period.recovery = False
    elif 5 * state['local_units'] > 3 * state['total_units']:
        period.recovery = True
    elif requested_mode is not None:
        period.recovery = requested_mode
    period.revision += 1

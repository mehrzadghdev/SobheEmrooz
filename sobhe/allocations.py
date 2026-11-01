"""Period-wide arithmetic and deterministic, advisory recommendations."""
import hashlib
import json
from fractions import Fraction

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import func
from .constants import CATEGORIES, WEIGHTS
from .models import Advertisement, Period, db
from .services import boxes, quota_boxes, decimal_string, period_key


def find_period(value):
    year, half = period_key(value)
    return Period.query.filter_by(year=year, half=half).first()


def validate_targets(targets):
    for category in CATEGORIES:
        if sum(target.share_bp for target in targets if target.category == category) != 10000:
            raise ValueError('جمع سهم رسانه‌ها در هر یک از سه گروه باید دقیقاً ۱۰۰ درصد باشد.')


def snapshot(period, *, exclude_id=None):
    targets = list(period.targets) if period else []
    query = db.session.query(Advertisement.outlet_id, func.sum(Advertisement.volume_units),
                             func.max(Advertisement.id)).filter(
        Advertisement.period_id == period.id, Advertisement.voided_at.is_(None)) if period else None
    if query is not None and exclude_id:
        query = query.filter(Advertisement.id != exclude_id)
    allocated = {row[0]: (int(row[1]), row[2]) for row in query.group_by(Advertisement.outlet_id)} if query is not None else {}
    category_units = {key: sum(allocated.get(target.outlet_id, (0, 0))[0] for target in targets if target.category == key)
                      for key in CATEGORIES}
    total = sum(category_units.values())
    result = {'total_units': total, 'total': boxes(total), 'categories': category_units,
              'local_units': category_units['local_one'] + category_units['local_two'],
              'rows': [], 'targets': targets, 'period': period}
    for target in targets:
        amount, last = allocated.get(target.outlet_id, (0, 0))
        category_total = category_units[target.category]
        expected = Fraction(total * WEIGHTS[target.category] * target.share_bp, 100 * 10000)
        result['rows'].append({
            'outlet_id': target.outlet_id, 'name': target.outlet.name, 'category': target.category,
            'share_bp': target.share_bp, 'active': target.outlet.active,
            'allocated_units': amount, 'allocated': boxes(amount), 'last': last,
            'actual_percent': decimal_string(Fraction(amount * 100, total)) if total else '0',
            'category_percent': decimal_string(Fraction(amount * 100, category_total)) if category_total else '0',
            'target_percent': decimal_string(Fraction(WEIGHTS[target.category] * target.share_bp, 10000)),
            'target_share': decimal_string(Fraction(target.share_bp, 100)),
            'expected_units': expected, 'expected': quota_boxes(expected), 'headroom': quota_boxes(max(0, expected - amount)),
            'exceeded': amount > expected, 'excess': quota_boxes(max(0, amount - expected)),
        })
    result['local_percent'] = decimal_string(Fraction(result['local_units'] * 100, total)) if total else '0'
    result['national_percent'] = decimal_string(Fraction(category_units['national'] * 100, total)) if total else '0'
    return result


def recommend(state, volume_units):
    total, local = state['total_units'], state['local_units']
    period = state['period']
    recovery = bool(period and period.recovery)
    # Manual changes may already have crossed a threshold since the prior mode.
    if not total or 2 * local <= total:
        recovery = False
    elif 5 * local > 3 * total:
        recovery = True
    if total and not recovery and 5 * (local + volume_units) > 3 * (total + volume_units):
        recovery = True
    categories = ['national'] if recovery else ['local_one', 'local_two']
    eligible = [row for row in state['rows'] if row['active'] and row['share_bp'] > 0]
    categories.sort(key=lambda key: (-(Fraction(total * WEIGHTS[key], 100) - state['categories'][key]), key))
    for category in categories:
        candidates = [row for row in eligible if row['category'] == category]
        if not candidates:
            continue
        if not state['categories'][category]:
            chosen = min(candidates, key=lambda row: (-row['share_bp'], row['outlet_id']))
        else:
            chosen = min(candidates, key=lambda row: (Fraction(row['allocated_units'], row['share_bp']),
                                                       row['last'] or 0, row['outlet_id']))
        return chosen, recovery
    return None, recovery


def public_row(row):
    return {key: value for key, value in row.items() if key not in ('expected_units', 'last')}


def preview(period, volume_units, selected_id=None, *, exclude_id=None, record_version=None):
    if not period or not period.active:
        raise ValueError('ابتدا مدیر باید برنامه سهم رسانه‌ها را برای این دوره فعال کند.')
    validate_targets(period.targets)
    state = snapshot(period, exclude_id=exclude_id)
    recommended, recovery = recommend(state, volume_units)
    selected_id = selected_id or (recommended['outlet_id'] if recommended else None)
    existing = db.session.get(Advertisement, exclude_id) if exclude_id else None
    retained_id = existing.outlet_id if existing and existing.period_id == period.id else None
    selected = next((row for row in state['rows'] if row['outlet_id'] == selected_id
                     and (row['active'] or row['outlet_id'] == retained_id)), None)
    if selected_id and selected is None:
        raise ValueError('رسانه انتخاب‌شده فعال نیست یا در برنامه این دوره وجود ندارد.')
    warnings = []
    if not recommended:
        warnings.append('در گروه موردنیاز رسانه واجد سهم فعال وجود ندارد؛ تنظیمات دوره را بررسی کنید.')
    if selected and recommended and selected_id != recommended['outlet_id']:
        warnings.append('رسانه انتخاب‌شده با پیشنهاد سامانه متفاوت است؛ تخصیص دستی ثبت خواهد شد.')
    total_after = state['total_units'] + volume_units
    selected_data = public_row(selected) if selected else None
    if selected:
        expected = Fraction(total_after * WEIGHTS[selected['category']] * selected['share_bp'], 100 * 10000)
        after = selected['allocated_units'] + volume_units
        if selected['exceeded']:
            warnings.append(f'رسانه {selected["name"]} در حال حاضر از سهمیه دوره عبور کرده است.')
        if after > expected:
            warnings.append(f'با این تخصیص، رسانه {selected["name"]} از سهمیه عبور می‌کند: '
                            f'{boxes(after)} کادر در برابر سهمیه {quota_boxes(expected)} کادر.')
        category_after = state['categories'][selected['category']] + volume_units
        selected_data.update(projected_allocated=boxes(after), projected_expected=quota_boxes(expected),
                             projected_percent=decimal_string(Fraction(after * 100, total_after)),
                             projected_category_percent=decimal_string(Fraction(after * 100, category_after)),
                             projected_exceeded=after > expected)
    local_after = state['local_units'] + (volume_units if selected and selected['category'] != 'national' else 0)
    if selected and 5 * local_after > 3 * total_after:
        warnings.append('سهم محلی پس از تخصیص از سقف ترجیحی ۶۰ درصد عبور می‌کند؛ پیشنهادهای بعدی به سمت سراسری می‌رود.')
    fingerprint = {
        'period': period.id, 'revision': period.revision, 'volume_units': volume_units,
        'selected': selected_id, 'excluded': exclude_id, 'record_version': record_version,
        'total': state['total_units'], 'local': state['local_units'], 'recovery': period.recovery,
        'recommended': recommended['outlet_id'] if recommended else None,
        'rows': [(r['outlet_id'], r['category'], r['share_bp'], r['active'], r['allocated_units']) for r in state['rows']],
        'warnings': warnings,
    }
    digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    token = URLSafeTimedSerializer(current_app.secret_key, salt='allocation-preview').dumps(digest)
    return {'period_id': period.id, 'period_year': period.year, 'period_half': period.half,
            'total': state['total'], 'local_percent': state['local_percent'],
            'projected_local_percent': decimal_string(Fraction(local_after * 100, total_after)),
            'recovery': recovery, 'recommended': public_row(recommended) if recommended else None,
            'selected': selected_data, 'warnings': warnings, 'token': token, 'fingerprint': digest,
            'options': [{'id': r['outlet_id'], 'name': r['name'], 'category': r['category']} for r in state['rows']
                        if r['active'] or r['outlet_id'] == retained_id]}


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

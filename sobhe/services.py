"""Validation, Persian dates, and audited application operations."""
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
from zoneinfo import ZoneInfo

import jdatetime
from flask import g
from .constants import CATEGORY_LABELS
from .models import AuditEvent, db

DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
MAX_NUMBER = 9_000_000_000_000_000


def text(value):
    return str(value if value is not None else '').strip().translate(DIGITS).replace('ي', 'ی').replace('ك', 'ک')


def required(data, key, maximum=150):
    value = text(data.get(key))
    if not value or len(value) > maximum:
        raise ValueError(f'فیلد {key} الزامی است و حداکثر {maximum} نویسه می‌پذیرد.')
    return value


def integer(value, minimum=0, maximum=MAX_NUMBER, optional=False):
    raw = text(value).replace(',', '').replace('٬', '')
    if optional and not raw:
        return None
    if not re.fullmatch(r'[0-9]+', raw) or not minimum <= int(raw) <= maximum:
        raise ValueError('عدد صحیح معتبر در محدوده مجاز وارد کنید.')
    return int(raw)


def units(value, *, positive=False, optional=False):
    raw = text(value).replace('٫', '.').replace('٬', '').replace(',', '')
    if optional and not raw:
        return None
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', raw):
        raise ValueError('کادر و درصد باید عدد نامنفی با حداکثر دو رقم اعشار باشند.')
    try:
        result = int(Decimal(raw) * 100)
    except (InvalidOperation, ValueError):
        raise ValueError('عدد اعشاری معتبر وارد کنید.') from None
    if result < (1 if positive else 0) or result > MAX_NUMBER:
        raise ValueError('حجم آگهی باید مثبت و در محدوده مجاز باشد.')
    return result


def decimal_string(value, digits=2):
    value = Fraction(value)
    result = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
        Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return format(result, 'f').rstrip('0').rstrip('.') if digits else str(result)


def boxes(value):
    return decimal_string(Fraction(value or 0, 100))


def quota_boxes(value):
    """Keep small quota differences visible; stored allocations still have two decimals."""
    return decimal_string(Fraction(value or 0, 100), digits=8)


def today():
    return datetime.now(ZoneInfo('Asia/Tehran')).date()


def parse_date(value, *, optional=False):
    raw = text(value)
    if not raw and optional:
        return None
    try:
        if '/' in raw:
            year, month, day = map(int, raw.split('/'))
            result = jdatetime.date(year, month, day).togregorian()
        else:
            result = date.fromisoformat(raw)
        year = jdatetime.date.fromgregorian(date=result).year
        if not 1200 <= year <= 1600:
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError):
        raise ValueError('تاریخ معتبر شمسی یا میلادی وارد کنید.') from None


def jalali(value):
    if not value:
        return '—'
    if isinstance(value, datetime):
        value = value.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('Asia/Tehran')).date()
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return jdatetime.date.fromgregorian(date=value).strftime('%Y/%m/%d')


def period_key(value=None):
    current = jdatetime.date.fromgregorian(date=value or today())
    return current.year


def period_bounds(year):
    start = jdatetime.date(year, 1, 1).togregorian()
    end = jdatetime.date(year + 1, 1, 1).togregorian()
    return start, end


def period_label(period):
    return f'{period.year} · فروردین تا اسفند'


def category_labels():
    return dict(CATEGORY_LABELS)


def audit(action, target, detail=None):
    db.session.add(AuditEvent(actor_id=getattr(getattr(g, 'user', None), 'id', None), action=action,
                              target=str(target), detail=json.dumps(detail or {}, ensure_ascii=False, default=str)))


def ad_snapshot(ad):
    return {column.name: getattr(ad, column.name) for column in ad.__table__.columns}

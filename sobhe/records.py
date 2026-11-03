"""Advertisement input validation and safe attachment storage."""
import io
import secrets
from pathlib import Path

from flask import current_app, g
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

from .allocations import find_period
from .constants import AD_TYPES, PRINT_STATUSES, PROVINCES, TEXT_FIELDS
from .models import Advertisement, Organization, Subject, db
from .services import integer, parse_date, text, units


def values(data, existing=None):
    result = {'ma_reference': integer(data.get('ma_reference'), 1),
              'organization_id': integer(data.get('organization_id'), 1),
              'outlet_id': integer(data.get('outlet_id'), 1),
              'allocation_date': parse_date(data.get('allocation_date')),
              'volume_units': units(data.get('volume'), positive=True)}
    duplicate = Advertisement.query.filter_by(ma_reference=result['ma_reference'])
    if existing:
        duplicate = duplicate.filter(Advertisement.id != existing.id)
    if duplicate.first():
        raise ValueError('شماره م/الف قبلاً ثبت شده است؛ برای هر آگهی شماره یکتا وارد کنید.')
    organization = db.session.get(Organization, result['organization_id'])
    if not organization or (not organization.active and (not existing or existing.organization_id != organization.id)):
        raise ValueError('سازمان آگهی‌دهنده فعال انتخاب کنید.')
    period = find_period(result['allocation_date'])
    if not period or not period.active:
        raise ValueError('دوره تاریخ تخصیص برای ثبت آگهی فعال نشده است.')
    result['period_id'] = period.id
    for key, limit in TEXT_FIELDS.items():
        result[key] = text(data.get(key))
        if len(result[key]) > limit:
            raise ValueError(f'متن {key} طولانی‌تر از حد مجاز است.')
    for key in ('proposed_date', 'final_print_date', 'approval_date', 'letter_date'):
        result[key] = parse_date(data.get(key), optional=True)
    for key in ('amount', 'approved_amount'):
        result[key] = integer(data.get(key), optional=True)
    result['page_number'] = integer(data.get('page_number'), 1, 100000, optional=True)
    result['headline_units'] = units(data.get('headline_volume'), optional=True)
    result['approved_units'] = units(data.get('approved_volume'), optional=True)
    result['subject_id'] = integer(data.get('subject_id'), 1, optional=True)
    if result['subject_id']:
        subject = db.session.get(Subject, result['subject_id'])
        if not subject or (not subject.active and (not existing or existing.subject_id != subject.id)):
            raise ValueError('موضوع معتبر انتخاب کنید.')
    for key, options in [('province', PROVINCES), ('ad_type', AD_TYPES), ('print_status', PRINT_STATUSES)]:
        result[key] = text(data.get(key)) or ('منتظر چاپ' if key == 'print_status' else '')
        if result[key] and result[key] not in options:
            raise ValueError('نوع آگهی، استان یا وضعیت چاپ معتبر نیست.')
    return result, period


def form_values(ad=None):
    from .services import boxes, today
    if not ad:
        return {'allocation_date': today().isoformat(), 'print_status': 'منتظر چاپ'}
    result = {column.name: getattr(ad, column.name) for column in ad.__table__.columns}
    result.update(volume=boxes(ad.volume_units),
                  headline_volume=boxes(ad.headline_units) if ad.headline_units is not None else '',
                  approved_volume=boxes(ad.approved_units) if ad.approved_units is not None else '')
    for key, value in result.items():
        if value is None:
            result[key] = ''
        elif hasattr(value, 'isoformat'):
            result[key] = value.isoformat()
    return result


def store_attachment(upload):
    if not upload or not upload.filename:
        return None
    content = upload.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise ValueError('حداکثر حجم فایل آگهی ۱۰ مگابایت است.')
    suffix = Path(upload.filename).suffix.lower()
    if suffix == '.pdf' and content.startswith(b'%PDF-') and b'%%EOF' in content[-2048:]:
        mime, extension = 'application/pdf', '.pdf'
    elif suffix in ('.jpg', '.jpeg', '.png'):
        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in ('JPEG', 'PNG') or image.width * image.height > 25_000_000:
                    raise ValueError('تصویر آگهی معتبر نیست یا ابعاد آن بسیار بزرگ است.')
                mime, extension = ('image/png', '.png') if image.format == 'PNG' else ('image/jpeg', '.jpg')
                image.verify()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise ValueError('فایل تصویر آگهی معتبر نیست.') from None
    else:
        raise ValueError('فقط فایل معتبر PDF، PNG یا JPEG پذیرفته می‌شود.')
    key = secrets.token_hex(24) + extension
    root = Path(current_app.config['STORAGE_ROOT'])
    root.mkdir(parents=True, exist_ok=True)
    path = root / key
    g.staged_files.append(path)
    with path.open('xb') as output:
        output.write(content)
    # Keep the original Persian filename as display metadata, never as a filesystem path.
    name = upload.filename.replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join(c for c in name if c.isprintable())[:240] or secure_filename(upload.filename) or ('advertisement' + extension)
    return dict(file_key=key, file_name=name, file_mime=mime)

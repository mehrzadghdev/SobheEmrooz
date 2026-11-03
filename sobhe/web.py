"""Public reports and authenticated, transactionally audited HTTP actions."""
import io
import re
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash

from . import allocations as a
from . import records as r
from . import reports as report
from . import services as s
from .membership import enroll_period, sync_outlet
from .constants import CATEGORIES, COLUMNS, ROLES
from .models import (Advertisement, AuditEvent, Organization, Outlet, OutletTarget,
                     Period, Setting, Subject, User, db, now)

bp = Blueprint('web', __name__)


def access(admin=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.user:
                if request.path.startswith('/api/'):
                    abort(401, 'برای ثبت و پیش‌نمایش تخصیص وارد حساب شوید.')
                return redirect(url_for('web.login'))
            if admin and g.user.role != 'admin':
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorate


def chosen_period():
    year, half = s.period_key()
    year = s.integer(request.args.get('year', year), 1200, 1600)
    half = s.integer(request.args.get('half', half), 1, 2)
    period = Period.query.filter_by(year=year, half=half).first()
    return period, SimpleNamespace(year=year, half=half)


def query_url(**changes):
    values = request.args.to_dict(flat=False)
    values.update(changes)
    return url_for(request.endpoint, **values)


@bp.app_context_processor
def helpers():
    management_endpoints = {'web.advertisements', 'web.ad_new', 'web.ad_edit', 'web.admin_ad_detail',
                            'web.ad_void', 'web.catalog', 'web.media_form', 'web.admin_media_detail',
                            'web.media_delete', 'web.periods', 'web.period_edit', 'web.category_settings',
                            'web.users', 'web.audit_log'}
    management = request.endpoint in management_endpoints or request.path.startswith('/admin/')
    return {'query_url': query_url, 'report_cell': report.cell,
            'page_space': 'admin' if management else 'public'}


@bp.route('/dashboard')
@bp.route('/')
def dashboard():
    try:
        period, selected = chosen_period()
    except ValueError as error:
        abort(400, str(error))
    state = a.snapshot(period)
    query = Advertisement.query.filter_by(period_id=period.id).filter(Advertisement.voided_at.is_(None)) if period else None
    return render_template('public/dashboard.html', selected=selected, period=period, state=state,
                           ad_count=query.count() if query is not None else 0,
                           recent=query.order_by(Advertisement.id.desc()).limit(6).all() if query is not None else [])


@bp.route('/reports')
def reports():
    return report_page()


@bp.route('/admin/advertisements')
@access()
def advertisements():
    return report_page(management=True)


def report_page(management=False):
    try:
        period, selected = chosen_period()
        view = 'ads' if management else request.args.get('view', 'ads')
        filter_args = request.args.copy()
        if management and 'date_field' not in filter_args:
            filter_args['date_field'] = 'allocation_date'
        query, filters = report.filtered_query(filter_args, selected.year, selected.half)
        columns = report.selected_columns(request.args)
        page = s.integer(request.args.get('page', '1'), 1, 1000000)
        per_page = s.integer(request.args.get('per_page', '25'), 10, 100)
        if view not in ('ads', 'media', 'organizations'):
            raise ValueError('نوع گزارش معتبر نیست.')
        if request.args.get('export') not in (None, '', 'xlsx'):
            raise ValueError('قالب خروجی معتبر نیست.')
    except ValueError as error:
        abort(400, str(error))
    totals = query.with_entities(func.count(Advertisement.id), func.coalesce(func.sum(Advertisement.volume_units), 0),
                                 func.coalesce(func.sum(Advertisement.amount), 0)).one()
    state = a.snapshot(period)
    organizations = query.join(Organization).with_entities(
        Organization.id.label('id'), Organization.name, func.count(Advertisement.id).label('count'),
        func.sum(Advertisement.volume_units).label('volume'), func.coalesce(func.sum(Advertisement.amount), 0).label('amount')
    ).group_by(Organization.id, Organization.name).order_by(func.sum(Advertisement.volume_units).desc()).all()
    if request.args.get('export') == 'xlsx':
        if view == 'media':
            headers = ['رسانه', 'گروه', 'حجم دریافتی (کادر)', 'درصد واقعی از کل', 'درصد واقعی در گروه']
            labels = s.category_labels()
            data = [[row['name'], labels[row['category']], row['allocated'], row['actual_percent'],
                     row['category_percent']] for row in state['rows']]
            group_headers = ['گروه', 'هدف از کل (درصد)', 'حجم دریافتی (کادر)', 'درصد واقعی از کل', 'حجم هدف (کادر)', 'وضعیت گروه']
            group_data = [[labels[key], group['target_percent'], group['allocated'], group['actual_percent'],
                           group['expected'], 'بیشتر از هدف گروه' if group['exceeded'] else 'در محدوده هدف گروه']
                          for key, group in state['groups'].items()]
            return spreadsheet(headers, data, extra_sheets=[('گروه‌ها', group_headers, group_data)])
        elif view == 'organizations':
            headers = ['سازمان آگهی‌دهنده', 'تعداد آگهی', 'حجم آگهی (کادر)', 'مبلغ آگهی (ریال)']
            data = [[row.name, row.count, s.boxes(row.volume), row.amount] for row in organizations]
        else:
            headers = [COLUMNS[key] for key in columns]
            records = query.options(joinedload(Advertisement.organization), joinedload(Advertisement.outlet),
                                     joinedload(Advertisement.subject)).order_by(Advertisement.id.desc()).all()
            data = [[url_for('web.attachment', ad_id=ad.id, _external=True) if key == 'file' and ad.file_key
                     else report.cell(ad, key) for key in columns] for ad in records]
        return spreadsheet(headers, data)
    rows = query.options(joinedload(Advertisement.organization), joinedload(Advertisement.outlet),
                          joinedload(Advertisement.subject)).order_by(Advertisement.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
    advanced_keys = ('id_min', 'id_max', 'ma_min', 'ma_max', 'amount_min', 'amount_max',
                     'approved_amount_min', 'approved_amount_max', 'province', 'subject_id',
                     'letter_number', 'subject_description', 'ad_text', 'ad_type', 'print_status')
    advanced_count = sum(bool(request.args.get(key)) for key in advanced_keys)
    return render_template('admin/advertisements.html' if management else 'public/reports.html',
                           management=management, advanced_count=advanced_count,
                           period=period, selected=selected, filters=filters, state=state,
                           selected_columns=columns, rows=rows, totals=totals, grouped_organizations=organizations,
                           page=page, per_page=per_page, pages=max(1, (totals[0] + per_page - 1) // per_page), view=view,
                           outlets=Outlet.query.order_by(Outlet.name).all(),
                           organizations=Organization.query.order_by(Organization.name).all(),
                           subjects=Subject.query.order_by(Subject.name).all())


def spreadsheet(headers, rows, *, extra_sheets=()):
    book = Workbook()
    sheet = book.active
    sheet.title = 'گزارش آگهی'
    sheets = [(sheet, headers, rows)] + [(book.create_sheet(title), labels, values) for title, labels, values in extra_sheets]
    for sheet, labels, values in sheets:
        sheet.sheet_view.rightToLeft = True
        sheet.append(labels)
        for cell in sheet[1]:
            cell.font = Font(color='FFFFFF', bold=True)
            cell.fill = PatternFill('solid', fgColor='DA1214')
        for row in values:
            sheet.append(["'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value
                          for value in row])
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(45, max(14, len(str(column[0].value)) + 3))
    output = io.BytesIO()
    book.save(output)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='advertisement-report.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=s.text(request.form.get('username'))).first()
        if user and user.active and check_password_hash(user.password_hash, request.form.get('password', '')):
            session.clear()
            session['user_id'] = user.id
            session['auth_version'] = user.auth_version
            session.permanent = True
            g.user = user
            s.audit('login', f'user:{user.id}')
            db.session.commit()
            return redirect(url_for('web.advertisements'))
        flash('نام کاربری یا رمز عبور معتبر نیست یا حساب غیرفعال است.', 'danger')
        return render_template('public/login.html'), 400
    return render_template('public/login.html')


@bp.route('/logout', methods=['POST'])
@access()
def logout():
    session.clear()
    return redirect(url_for('web.dashboard'))


@bp.route('/api/allocation-preview')
@access()
def allocation_preview():
    try:
        volume = s.units(request.args.get('volume'), positive=True)
        allocation_date = s.parse_date(request.args.get('allocation_date'))
        outlet_id = s.integer(request.args.get('outlet_id'), 1, optional=True)
        exclude_id = s.integer(request.args.get('exclude_id'), 1, optional=True)
        existing = None
        if exclude_id:
            if g.user.role != 'admin':
                abort(403)
            existing = db.get_or_404(Advertisement, exclude_id)
            if existing.voided_at:
                raise ValueError('آگهی باطل‌شده قابل ویرایش نیست.')
        result = a.preview(a.find_period(allocation_date), volume, outlet_id, exclude_id=exclude_id,
                           record_version=existing.version if existing else None)
        result.pop('fingerprint')
        return jsonify(result)
    except ValueError as error:
        return jsonify(error=str(error)), 400


def ad_form(data, ad=None, preview_data=None, status=200):
    try:
        allocation_date = s.parse_date(data.get('allocation_date') or s.today().isoformat())
        period = a.find_period(allocation_date)
    except ValueError:
        period = a.find_period(s.today())
    targets = period.targets if period else []
    return render_template('admin/ad-form.html', data=data, ad=ad, preview=preview_data, period=period,
                           targets=targets, organizations=Organization.query.order_by(Organization.name).all(),
                           subjects=Subject.query.order_by(Subject.name).all()), status


@bp.route('/advertisements/new', methods=['GET', 'POST'])
@bp.route('/admin/advertisements/new', methods=['GET', 'POST'])
@access()
def ad_new():
    return save_ad()


@bp.route('/advertisements/<int:ad_id>/edit', methods=['GET', 'POST'])
@bp.route('/admin/advertisements/<int:ad_id>/edit', methods=['GET', 'POST'])
@access(admin=True)
def ad_edit(ad_id):
    ad = db.get_or_404(Advertisement, ad_id)
    if ad.voided_at:
        abort(400, 'آگهی باطل‌شده قابل ویرایش نیست.')
    return save_ad(ad)


def save_ad(ad=None):
    if request.method == 'GET':
        data = r.form_values(ad)
        initial = None
        if ad:
            try:
                initial = a.preview(ad.period, ad.volume_units, ad.outlet_id, exclude_id=ad.id, record_version=ad.version)
            except ValueError:
                pass
        return ad_form(data, ad, initial)
    data = request.form.to_dict()
    preview_data = None
    try:
        if ad and s.integer(data.get('version'), 1) != ad.version:
            raise ValueError('رکورد هم‌زمان تغییر کرده است؛ صفحه را تازه کنید و دوباره بررسی کنید.')
        values, period = r.values(data, ad)
        preview_data = a.preview(period, values['volume_units'], values['outlet_id'],
                                 exclude_id=ad.id if ad else None, record_version=ad.version if ad else None)
        if not a.token_matches(data.get('preview_token'), preview_data):
            flash('پیش‌نمایش تازه شد؛ سهم‌ها و هشدارها را بررسی و دوباره ثبت کنید. در صورت انتخاب فایل، آن را دوباره انتخاب کنید.', 'warning')
            data['override_confirmed'] = ''
            return ad_form(data, ad, preview_data, 409)
        if preview_data['warnings'] and data.get('override_confirmed') != '1':
            flash('برای ادامه تخصیص دستی، پذیرش هشدارهای سهمیه را علامت بزنید.', 'warning')
            return ad_form(data, ad, preview_data, 400)
        before = s.ad_snapshot(ad) if ad else None
        old_period_id = ad.period_id if ad else None
        if not ad:
            ad = Advertisement(creator_id=g.user.id)
            db.session.add(ad)
        else:
            ad.version += 1
        for key, value in values.items():
            setattr(ad, key, value)
        ad.period = period
        attachment = r.store_attachment(request.files.get('file'))
        if attachment:
            for key, value in attachment.items():
                setattr(ad, key, value)
        elif data.get('remove_file') == '1':
            ad.file_key = ad.file_name = ad.file_mime = None
        db.session.flush()
        a.update_recovery(period, preview_data['recovery'])
        if old_period_id and old_period_id != period.id:
            a.update_recovery(db.session.get(Period, old_period_id))
        s.audit('advertisement_edit' if before else 'advertisement_create', f'ad:{ad.id}',
                {'before': before, 'after': s.ad_snapshot(ad), 'warnings': preview_data['warnings'],
                 'recommended_outlet_id': preview_data['recommended']['outlet_id'] if preview_data['recommended'] else None,
                 'override_confirmed': data.get('override_confirmed') == '1'})
        db.session.commit()
        g.staged_files = []
        flash('آگهی و تخصیص با موفقیت ثبت شد.', 'success')
        return redirect(url_for('web.admin_ad_detail', ad_id=ad.id))
    except (ValueError, SQLAlchemyError) as error:
        db.session.rollback()
        for path in g.staged_files:
            path.unlink(missing_ok=True)
        g.staged_files = []
        if isinstance(error, SQLAlchemyError):
            current_app.logger.exception('Advertisement save failed')
            message = 'ذخیره نشد؛ شماره م/الف تکراری یا تغییر هم‌زمان وجود دارد. دوباره بررسی کنید.'
        else:
            message = str(error)
        flash(message, 'danger')
        # A failed insert has no persistent identity; keep the creation form in creation mode.
        existing = db.session.get(Advertisement, before['id']) if 'before' in locals() and before else None
        if existing is None and request.endpoint == 'web.ad_edit':
            existing = db.get_or_404(Advertisement, request.view_args['ad_id'])
        return ad_form(data, existing, preview_data, 400)


@bp.route('/advertisements/<int:ad_id>')
def ad_detail(ad_id):
    return advertisement_detail(ad_id)


@bp.route('/admin/advertisements/<int:ad_id>')
@access()
def admin_ad_detail(ad_id):
    return advertisement_detail(ad_id, management=True)


def advertisement_detail(ad_id, management=False):
    ad = db.get_or_404(Advertisement, ad_id)
    if ad.voided_at and (not management or g.user.role != 'admin'):
        abort(404)
    state = a.snapshot(ad.period)
    outlet_stats = next((row for row in state['rows'] if row['outlet_id'] == ad.outlet_id), None)
    return render_template('admin/ad-detail.html' if management else 'public/ad-detail.html',
                           ad=ad, stats=outlet_stats,
                           group=state['groups'][outlet_stats['category']] if outlet_stats else None)


@bp.route('/advertisements/<int:ad_id>/file')
def attachment(ad_id):
    ad = db.get_or_404(Advertisement, ad_id)
    if ad.voided_at or not ad.file_key:
        abort(404)
    path = Path(current_app.config['STORAGE_ROOT']) / ad.file_key
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=ad.file_name, mimetype=ad.file_mime)


@bp.route('/advertisements/<int:ad_id>/void', methods=['POST'])
@bp.route('/admin/advertisements/<int:ad_id>/void', methods=['POST'])
@access(admin=True)
def ad_void(ad_id):
    ad = db.get_or_404(Advertisement, ad_id)
    try:
        if ad.voided_at:
            raise ValueError('آگهی قبلاً باطل شده است.')
        if ad.version != s.integer(request.form.get('version'), 1):
            raise ValueError('رکورد تغییر کرده است؛ صفحه را تازه کنید.')
        reason = s.required(request.form, 'reason', 1000)
        ad.voided_at, ad.void_reason = now(), reason
        ad.version += 1
        db.session.flush()
        a.update_recovery(ad.period)
        s.audit('advertisement_void', f'ad:{ad.id}', {'reason': reason})
        db.session.commit()
        flash('آگهی از گزارش عمومی و محاسبات سهمیه حذف شد؛ تاریخچه و شماره م/الف حفظ شد.', 'success')
    except ValueError as error:
        db.session.rollback()
        flash(str(error), 'danger')
    if request.form.get('return_to') == 'reports':
        return redirect(url_for('web.advertisements', year=ad.period.year, half=ad.period.half))
    return redirect(url_for('web.admin_ad_detail', ad_id=ad_id))


CATALOGS = {'outlets': (Outlet, 'رسانه‌ها'), 'organizations': (Organization, 'سازمان‌های آگهی‌دهنده'),
            'subjects': (Subject, 'موضوع‌های آگهی')}


@bp.route('/admin/catalog/<kind>', methods=['GET', 'POST'])
@access(admin=True)
def catalog(kind):
    if kind not in CATALOGS:
        abort(404)
    model, title = CATALOGS[kind]
    if request.method == 'POST':
        row_id = None
        try:
            row_id = s.integer(request.form.get('id'), 1, optional=True)
            row = db.get_or_404(model, row_id) if row_id else model()
            before = {'name': row.name, 'active': row.active} if row_id else None
            if kind == 'outlets':
                if before:
                    before['category'] = row.category
                media_type = request.form.get('media_type')
                category = 'national' if media_type == 'national' else request.form.get('local_group')
                if media_type not in ('national', 'local') or category not in CATEGORIES or (media_type == 'local' and category == 'national'):
                    raise ValueError('نوع رسانه و برای رسانه محلی، یکی از دو گروه محلی را انتخاب کنید.')
                row.category = category
            row.name = s.required(request.form, 'name', 150 if kind != 'organizations' else 200)
            row.active = request.form.get('active') == '1'
            db.session.add(row)
            db.session.flush()
            if kind == 'outlets':
                membership = sync_outlet(row)
            s.audit('catalog_update' if row_id else 'catalog_create', f'{kind}:{row.id}',
                    {'before': before, 'name': row.name, 'active': row.active,
                     **({'category': row.category, 'membership': membership} if kind == 'outlets' else {})})
            db.session.commit()
            flash('اطلاعات ذخیره شد.', 'success')
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else 'این نام قبلاً ثبت شده است.', 'danger')
            if kind == 'outlets':
                return render_template('admin/media-form.html', outlet=db.session.get(Outlet, row_id) if row_id else None,
                                       data=request.form), 400
        return redirect(url_for('web.catalog', kind=kind))
    rows = model.query.order_by(model.name).all()
    if kind == 'outlets':
        used_ids = {row[0] for row in db.session.query(Advertisement.outlet_id).distinct()}
        used_ids.update(row[0] for row in db.session.query(OutletTarget.outlet_id).distinct())
        return render_template('admin/media.html', rows=rows, used_ids=used_ids)
    reference = Advertisement.organization_id if kind == 'organizations' else Advertisement.subject_id
    used_ids = {row[0] for row in db.session.query(reference).filter(reference.is_not(None)).distinct()}
    return render_template('admin/catalog.html', kind=kind, title=title, rows=rows, used_ids=used_ids)


@bp.route('/admin/catalog/<kind>/<int:row_id>/delete', methods=['POST'])
@access(admin=True)
def catalog_delete(kind, row_id):
    if kind not in ('organizations', 'subjects'):
        abort(404)
    model, _ = CATALOGS[kind]
    row = db.get_or_404(model, row_id)
    before = {'id': row.id, 'name': row.name, 'active': row.active}
    reference = Advertisement.organization_id if kind == 'organizations' else Advertisement.subject_id
    label = 'سازمان' if kind == 'organizations' else 'موضوع'
    if Advertisement.query.filter(reference == row.id).first():
        row.active = False
        s.audit('catalog_archive', f'{kind}:{row.id}', {'before': before, 'active': False})
        flash(f'{label} بایگانی شد؛ سوابق آگهی‌ها حفظ شدند.', 'success')
    else:
        s.audit('catalog_delete', f'{kind}:{row.id}', {'before': before})
        db.session.delete(row)
        flash(f'{label} حذف شد.', 'success')
    db.session.commit()
    return redirect(url_for('web.catalog', kind=kind))


@bp.route('/admin/media/new', defaults={'outlet_id': None})
@bp.route('/admin/media/<int:outlet_id>/edit')
@access(admin=True)
def media_form(outlet_id):
    outlet = db.get_or_404(Outlet, outlet_id) if outlet_id else None
    return render_template('admin/media-form.html', outlet=outlet, data={
        'name': outlet.name if outlet else '', 'active': '1' if not outlet or outlet.active else '',
        'media_type': ('national' if outlet.category == 'national' else 'local') if outlet and outlet.category else '',
        'local_group': outlet.category if outlet and outlet.category != 'national' else ''})


@bp.route('/media/<int:outlet_id>')
def media_detail(outlet_id):
    return outlet_detail(outlet_id)


@bp.route('/admin/media/<int:outlet_id>')
@access(admin=True)
def admin_media_detail(outlet_id):
    return outlet_detail(outlet_id, management=True)


def outlet_detail(outlet_id, management=False):
    outlet = db.get_or_404(Outlet, outlet_id)
    try:
        period, selected = chosen_period()
    except ValueError as error:
        abort(400, str(error))
    state = a.snapshot(period)
    stats = next((row for row in state['rows'] if row['outlet_id'] == outlet.id), None)
    query = Advertisement.query.filter_by(outlet_id=outlet.id, period_id=period.id if period else None).filter(Advertisement.voided_at.is_(None))
    return render_template('admin/media-detail.html' if management else 'public/media-detail.html',
                           management=management, outlet=outlet, stats=stats, selected=selected,
                           group=state['groups'][stats['category']] if stats else None,
                           ad_count=query.count(), recent=query.order_by(Advertisement.id.desc()).limit(10).all())


@bp.route('/admin/media/<int:outlet_id>/delete', methods=['POST'])
@access(admin=True)
def media_delete(outlet_id):
    outlet = db.get_or_404(Outlet, outlet_id)
    before = {'id': outlet.id, 'name': outlet.name, 'active': outlet.active, 'category': outlet.category}
    referenced = Advertisement.query.filter_by(outlet_id=outlet.id).first() or OutletTarget.query.filter_by(outlet_id=outlet.id).first()
    if referenced:
        outlet.active = False
        for period in Period.query.join(OutletTarget).filter(OutletTarget.outlet_id == outlet.id).all():
            period.revision += 1
        s.audit('media_archive', f'outlet:{outlet.id}', {'before': before, 'active': False})
        flash('رسانه بایگانی و از پیشنهادهای جدید خارج شد؛ سوابق آگهی و سهم دوره‌ها حفظ شدند.', 'success')
    else:
        s.audit('media_delete', f'outlet:{outlet.id}', {'before': before})
        db.session.delete(outlet)
        flash('رسانه حذف شد.', 'success')
    db.session.commit()
    return redirect(url_for('web.catalog', kind='outlets'))


@bp.route('/admin/periods', methods=['GET', 'POST'])
@access(admin=True)
def periods():
    if request.method == 'POST':
        try:
            year, half = s.integer(request.form.get('year'), 1200, 1600), s.integer(request.form.get('half'), 1, 2)
            if Period.query.filter_by(year=year, half=half).first():
                raise ValueError('این دوره قبلاً ایجاد شده است.')
            period = Period(year=year, half=half)
            db.session.add(period)
            db.session.flush()
            count = enroll_period(period)
            s.audit('period_create', f'period:{period.id}', {'year': year, 'half': half, 'media_count': count})
            db.session.commit()
            return redirect(url_for('web.period_edit', period_id=period.id))
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else 'دوره تکراری است.', 'danger')
    used_ids = {row[0] for row in db.session.query(Advertisement.period_id).distinct()}
    return render_template('admin/periods.html', periods=Period.query.order_by(Period.year.desc(), Period.half.desc()).all(),
                           used_ids=used_ids)


@bp.route('/admin/periods/<int:period_id>/delete', methods=['POST'])
@access(admin=True)
def period_delete(period_id):
    period = db.get_or_404(Period, period_id)
    try:
        if s.integer(request.form.get('revision'), 1) != period.revision:
            raise ValueError('برنامه دوره تغییر کرده است؛ صفحه را تازه کنید.')
        if Advertisement.query.filter_by(period_id=period.id).first():
            raise ValueError('دوره دارای سابقه آگهی قابل حذف نیست.')
        before = {'id': period.id, 'year': period.year, 'half': period.half,
                  'active': period.active, 'revision': period.revision,
                  'members': [{'outlet_id': target.outlet_id, 'category': target.category}
                              for target in period.targets]}
        for target in list(period.targets):
            db.session.delete(target)
        db.session.flush()
        s.audit('period_delete', f'period:{period.id}', {'before': before})
        db.session.delete(period)
        db.session.commit()
        flash('دوره و عضویت رسانه‌های آن حذف شدند.', 'success')
    except ValueError as error:
        db.session.rollback()
        flash(str(error), 'danger')
    return redirect(url_for('web.periods'))


@bp.route('/admin/periods/<int:period_id>', methods=['GET', 'POST'])
@access(admin=True)
def period_edit(period_id):
    period = db.get_or_404(Period, period_id)
    if request.method == 'POST':
        try:
            if s.integer(request.form.get('revision'), 1) != period.revision:
                raise ValueError('برنامه دوره تغییر کرده است؛ صفحه را تازه کنید.')
            if any(key.startswith(('include_', 'category_', 'share_')) for key in request.form):
                raise ValueError('نوع رسانه در بخش رسانه‌ها تعیین می‌شود؛ سهم هدف فقط برای گروه‌ها و ثابت است.')
            before = period.active
            period.active = request.form.get('active') == '1'
            a.update_recovery(period)
            s.audit('period_update', f'period:{period.id}', {'before_active': before, 'active': period.active})
            db.session.commit()
            flash('وضعیت دوره ذخیره شد؛ سهم گروه‌ها به‌صورت خودکار اعمال می‌شود.', 'success')
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else 'برنامه ذخیره نشد؛ دوباره بررسی کنید.', 'danger')
        return redirect(url_for('web.period_edit', period_id=period_id))
    return render_template('admin/period-edit.html', period=period, state=a.snapshot(period),
                           unclassified_count=Outlet.query.filter(Outlet.category.is_(None)).count())


@bp.route('/admin/category-labels', methods=['POST'])
@access(admin=True)
def category_settings():
    try:
        for key in ('local_one', 'local_two'):
            label = s.required(request.form, key, 150)
            setting = db.session.get(Setting, 'label_' + key)
            if setting:
                setting.value = label
            else:
                db.session.add(Setting(key='label_' + key, value=label))
        s.audit('category_labels_update', 'settings', {key: request.form[key] for key in ('local_one', 'local_two')})
        db.session.commit()
        flash('نام گروه‌های محلی ذخیره شد.', 'success')
    except ValueError as error:
        db.session.rollback()
        flash(str(error), 'danger')
    return redirect(url_for('web.periods'))


@bp.route('/admin/users', methods=['GET', 'POST'])
@access(admin=True)
def users():
    if request.method == 'POST':
        try:
            user_id = s.integer(request.form.get('id'), 1, optional=True)
            user = db.get_or_404(User, user_id) if user_id else User()
            username = s.required(request.form, 'username', 80)
            if not re.fullmatch(r'[\w.@+-]{3,80}', username):
                raise ValueError('نام کاربری باید ۳ تا ۸۰ نویسه و بدون فاصله باشد.')
            role, active = request.form.get('role'), request.form.get('active') == '1'
            if role not in ROLES:
                raise ValueError('نقش معتبر انتخاب کنید.')
            if user_id and user.role == 'admin' and user.active and (role != 'admin' or not active):
                if User.query.filter(User.id != user.id, User.role == 'admin', User.active.is_(True)).count() == 0:
                    raise ValueError('آخرین مدیر فعال سامانه قابل غیرفعال‌سازی یا تغییر نقش نیست.')
            password = request.form.get('password', '')
            if not user_id or password:
                if not 8 <= len(password) <= 200:
                    raise ValueError('رمز عبور باید ۸ تا ۲۰۰ نویسه باشد.')
                user.password_hash = generate_password_hash(password)
            user.username, user.name, user.role, user.active = username, s.required(request.form, 'name'), role, active
            user.auth_version = (user.auth_version or 0) + 1
            db.session.add(user)
            db.session.flush()
            s.audit('user_update' if user_id else 'user_create', f'user:{user.id}',
                    {'username': username, 'name': user.name, 'role': role, 'active': active, 'password_changed': bool(password)})
            db.session.commit()
            flash('حساب کاربری ذخیره شد.', 'success')
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else 'نام کاربری قبلاً ثبت شده است.', 'danger')
        return redirect(url_for('web.users'))
    used_ids = {row[0] for row in db.session.query(Advertisement.creator_id).distinct()}
    used_ids.update(row[0] for row in db.session.query(AuditEvent.actor_id).filter(AuditEvent.actor_id.is_not(None)).distinct())
    return render_template('admin/users.html', users=User.query.order_by(User.id).all(), used_ids=used_ids)


@bp.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@access(admin=True)
def user_delete(user_id):
    user = db.get_or_404(User, user_id)
    try:
        if user.active and user.role == 'admin' and not User.query.filter(
                User.id != user.id, User.role == 'admin', User.active.is_(True)).count():
            raise ValueError('آخرین مدیر فعال سامانه قابل حذف یا بایگانی نیست.')
        if user.id == g.user.id:
            raise ValueError('حسابی که با آن وارد شده‌اید قابل حذف یا بایگانی نیست.')
        if s.integer(request.form.get('auth_version'), 1) != user.auth_version:
            raise ValueError('حساب کاربری تغییر کرده است؛ صفحه را تازه کنید.')
        before = {'id': user.id, 'username': user.username, 'name': user.name, 'role': user.role, 'active': user.active}
        referenced = Advertisement.query.filter_by(creator_id=user.id).first() or AuditEvent.query.filter_by(actor_id=user.id).first()
        if referenced:
            user.active = False
            user.auth_version += 1
            s.audit('user_archive', f'user:{user.id}', {'before': before, 'active': False})
            flash('حساب بایگانی و دسترسی آن بسته شد؛ سوابق فعالیت حفظ شدند.', 'success')
        else:
            s.audit('user_delete', f'user:{user.id}', {'before': before})
            db.session.delete(user)
            flash('حساب کاربری حذف شد.', 'success')
        db.session.commit()
    except ValueError as error:
        db.session.rollback()
        flash(str(error), 'danger')
    return redirect(url_for('web.users'))


@bp.route('/admin/audit')
@access(admin=True)
def audit_log():
    try:
        page = s.integer(request.args.get('page', '1'), 1, 1000000)
    except ValueError as error:
        abort(400, str(error))
    query = AuditEvent.query.order_by(AuditEvent.id.desc())
    return render_template('admin/audit.html', events=query.offset((page - 1) * 30).limit(30).all(), page=page,
                           pages=max(1, (query.count() + 29) // 30))

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
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash

from . import allocations as a
from . import records as r
from . import reports as report
from . import services as s
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
    return {'query_url': query_url, 'report_cell': report.cell}


@bp.route('/')
@bp.route('/dashboard')
def dashboard():
    try:
        period, selected = chosen_period()
    except ValueError as error:
        abort(400, str(error))
    state = a.snapshot(period)
    query = Advertisement.query.filter_by(period_id=period.id).filter(Advertisement.voided_at.is_(None)) if period else None
    return render_template('dashboard.html', selected=selected, period=period, state=state,
                           ad_count=query.count() if query is not None else 0,
                           recent=query.order_by(Advertisement.id.desc()).limit(6).all() if query is not None else [])


@bp.route('/reports')
def reports():
    try:
        period, selected = chosen_period()
        view = request.args.get('view', 'ads')
        filter_args = request.args.copy()
        if g.user and view == 'ads' and 'date_field' not in filter_args:
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
        Organization.name, func.count(Advertisement.id).label('count'),
        func.sum(Advertisement.volume_units).label('volume'), func.coalesce(func.sum(Advertisement.amount), 0).label('amount')
    ).group_by(Organization.id, Organization.name).order_by(func.sum(Advertisement.volume_units).desc()).all()
    if request.args.get('export') == 'xlsx':
        if view == 'media':
            headers = ['رسانه', 'گروه', 'کادر تخصیص‌یافته', 'درصد کل واقعی', 'درصد هدف کل', 'سهم هدف در گروه', 'سهمیه کادر', 'وضعیت']
            labels = s.category_labels()
            data = [[row['name'], labels[row['category']], row['allocated'], row['actual_percent'], row['target_percent'],
                     row['target_share'], row['expected'], 'عبور از سهمیه' if row['exceeded'] else 'در محدوده سهمیه'] for row in state['rows']]
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
    return render_template('reports.html', period=period, selected=selected, filters=filters, state=state,
                           selected_columns=columns, rows=rows, totals=totals, grouped_organizations=organizations,
                           page=page, per_page=per_page, pages=max(1, (totals[0] + per_page - 1) // per_page), view=view,
                           outlets=Outlet.query.order_by(Outlet.name).all(),
                           organizations=Organization.query.order_by(Organization.name).all(),
                           subjects=Subject.query.order_by(Subject.name).all())


def spreadsheet(headers, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = 'گزارش آگهی'
    sheet.sheet_view.rightToLeft = True
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(color='FFFFFF', bold=True)
        cell.fill = PatternFill('solid', fgColor='DA1214')
    for row in rows:
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
            return redirect(url_for('web.dashboard'))
        flash('نام کاربری یا رمز عبور معتبر نیست یا حساب غیرفعال است.', 'danger')
        return render_template('login.html'), 400
    return render_template('login.html')


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
    return render_template('ad-form.html', data=data, ad=ad, preview=preview_data, period=period,
                           targets=targets, organizations=Organization.query.order_by(Organization.name).all(),
                           subjects=Subject.query.order_by(Subject.name).all()), status


@bp.route('/advertisements/new', methods=['GET', 'POST'])
@access()
def ad_new():
    return save_ad()


@bp.route('/advertisements/<int:ad_id>/edit', methods=['GET', 'POST'])
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
        return redirect(url_for('web.ad_detail', ad_id=ad.id))
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
    ad = db.get_or_404(Advertisement, ad_id)
    if ad.voided_at and (not g.user or g.user.role != 'admin'):
        abort(404)
    state = a.snapshot(ad.period)
    outlet_stats = next((row for row in state['rows'] if row['outlet_id'] == ad.outlet_id), None)
    return render_template('ad-detail.html', ad=ad, stats=outlet_stats)


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
        return redirect(url_for('web.reports', year=ad.period.year, half=ad.period.half, date_field='allocation_date'))
    return redirect(url_for('web.ad_detail', ad_id=ad_id))


CATALOGS = {'outlets': (Outlet, 'رسانه‌ها'), 'organizations': (Organization, 'سازمان‌های آگهی‌دهنده'),
            'subjects': (Subject, 'موضوع‌های آگهی')}


@bp.route('/admin/catalog/<kind>', methods=['GET', 'POST'])
@access(admin=True)
def catalog(kind):
    if kind not in CATALOGS:
        abort(404)
    model, title = CATALOGS[kind]
    if request.method == 'POST':
        try:
            row_id = s.integer(request.form.get('id'), 1, optional=True)
            row = db.get_or_404(model, row_id) if row_id else model()
            before = {'name': row.name, 'active': row.active} if row_id else None
            row.name = s.required(request.form, 'name', 150 if kind != 'organizations' else 200)
            row.active = request.form.get('active') == '1'
            db.session.add(row)
            db.session.flush()
            if kind == 'outlets':
                for period in Period.query.join(OutletTarget).filter(OutletTarget.outlet_id == row.id).all():
                    period.revision += 1
            s.audit('catalog_update' if row_id else 'catalog_create', f'{kind}:{row.id}',
                    {'before': before, 'name': row.name, 'active': row.active})
            db.session.commit()
            flash('اطلاعات ذخیره شد.', 'success')
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else 'این نام قبلاً ثبت شده است.', 'danger')
        return redirect(url_for('web.catalog', kind=kind))
    rows = model.query.order_by(model.name).all()
    if kind == 'outlets':
        used_ids = {row[0] for row in db.session.query(Advertisement.outlet_id).distinct()}
        used_ids.update(row[0] for row in db.session.query(OutletTarget.outlet_id).distinct())
        return render_template('media.html', rows=rows, used_ids=used_ids)
    return render_template('catalog.html', kind=kind, title=title, rows=rows)


@bp.route('/admin/media/new', defaults={'outlet_id': None})
@bp.route('/admin/media/<int:outlet_id>/edit')
@access(admin=True)
def media_form(outlet_id):
    outlet = db.get_or_404(Outlet, outlet_id) if outlet_id else None
    return render_template('media-form.html', outlet=outlet)


@bp.route('/media/<int:outlet_id>')
def media_detail(outlet_id):
    outlet = db.get_or_404(Outlet, outlet_id)
    try:
        period, selected = chosen_period()
    except ValueError as error:
        abort(400, str(error))
    state = a.snapshot(period)
    stats = next((row for row in state['rows'] if row['outlet_id'] == outlet.id), None)
    query = Advertisement.query.filter_by(outlet_id=outlet.id, period_id=period.id if period else None).filter(Advertisement.voided_at.is_(None))
    return render_template('media-detail.html', outlet=outlet, stats=stats, selected=selected,
                           ad_count=query.count(), recent=query.order_by(Advertisement.id.desc()).limit(10).all())


@bp.route('/admin/media/<int:outlet_id>/delete', methods=['POST'])
@access(admin=True)
def media_delete(outlet_id):
    outlet = db.get_or_404(Outlet, outlet_id)
    before = {'id': outlet.id, 'name': outlet.name, 'active': outlet.active}
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
            previous = Period.query.filter(or_(Period.year < year, (Period.year == year) & (Period.half < half))).order_by(
                Period.year.desc(), Period.half.desc()).first()
            if previous:
                for target in previous.targets:
                    db.session.add(OutletTarget(period_id=period.id, outlet_id=target.outlet_id,
                                                 category=target.category, share_bp=target.share_bp))
            s.audit('period_create', f'period:{period.id}', {'year': year, 'half': half, 'copied_from': previous.id if previous else None})
            db.session.commit()
            return redirect(url_for('web.period_edit', period_id=period.id))
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else 'دوره تکراری است.', 'danger')
    return render_template('periods.html', periods=Period.query.order_by(Period.year.desc(), Period.half.desc()).all())


@bp.route('/admin/periods/<int:period_id>', methods=['GET', 'POST'])
@access(admin=True)
def period_edit(period_id):
    period = db.get_or_404(Period, period_id)
    if request.method == 'POST':
        try:
            if s.integer(request.form.get('revision'), 1) != period.revision:
                raise ValueError('برنامه دوره تغییر کرده است؛ صفحه را تازه کنید.')
            proposed = []
            for outlet in Outlet.query.all():
                if request.form.get(f'include_{outlet.id}') != '1':
                    continue
                category = request.form.get(f'category_{outlet.id}')
                if category not in CATEGORIES:
                    raise ValueError('گروه رسانه معتبر نیست.')
                share = s.units(request.form.get(f'share_{outlet.id}'))
                if share > 10000:
                    raise ValueError('سهم رسانه نمی‌تواند بیشتر از ۱۰۰ درصد باشد.')
                proposed.append(SimpleNamespace(outlet_id=outlet.id, category=category, share_bp=share))
            used = {row[0] for row in db.session.query(Advertisement.outlet_id).filter_by(period_id=period.id).distinct()}
            if not used.issubset({target.outlet_id for target in proposed}):
                raise ValueError('رسانه دارای سابقه آگهی در این دوره قابل حذف از برنامه نیست؛ سهم آن را تغییر دهید.')
            active = request.form.get('active') == '1'
            if active or used:
                a.validate_targets(proposed)
            before = [(target.outlet_id, target.category, target.share_bp) for target in period.targets]
            existing = {target.outlet_id: target for target in period.targets}
            for target in proposed:
                row = existing.pop(target.outlet_id, None)
                if row is None:
                    row = OutletTarget(period_id=period.id, outlet_id=target.outlet_id)
                    db.session.add(row)
                row.category, row.share_bp = target.category, target.share_bp
            for row in existing.values():
                db.session.delete(row)
            period.active = active
            db.session.flush()
            db.session.expire(period, ['targets'])
            a.update_recovery(period)
            s.audit('period_targets_update', f'period:{period.id}', {'before': before,
                    'after': [(t.outlet_id, t.category, t.share_bp) for t in period.targets], 'active': active})
            db.session.commit()
            flash('سهم‌های همین دوره ذخیره و محاسبات به‌روز شد.', 'success')
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(error) if isinstance(error, ValueError) else 'برنامه ذخیره نشد؛ دوباره بررسی کنید.', 'danger')
        return redirect(url_for('web.period_edit', period_id=period_id))
    targets = {target.outlet_id: target for target in period.targets}
    totals = {key: sum(t.share_bp for t in targets.values() if t.category == key) for key in CATEGORIES}
    return render_template('period-edit.html', period=period, targets=targets, share_totals=totals,
                           outlets=Outlet.query.order_by(Outlet.name).all())


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
    return render_template('users.html', users=User.query.order_by(User.id).all())


@bp.route('/admin/audit')
@access(admin=True)
def audit_log():
    try:
        page = s.integer(request.args.get('page', '1'), 1, 1000000)
    except ValueError as error:
        abort(400, str(error))
    query = AuditEvent.query.order_by(AuditEvent.id.desc())
    return render_template('audit.html', events=query.offset((page - 1) * 30).limit(30).all(), page=page,
                           pages=max(1, (query.count() + 29) // 30))

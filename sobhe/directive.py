"""Versioned internal implementation of directive 228604 and its 1403 tariff.

Official decisions are recorded by staff; this module never submits to e-rasaneh.
"""
import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

import jdatetime
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from .models import db, Outlet, Advertisement, Period, now
from .services import audit, integer, parse_date, today, period_bounds

SOURCE = 'https://e-rasaneh.ir/Page_Show.aspx?PageID=24'

class DirectiveRecord(db.Model):
    __tablename__ = 'directive_record'
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(30), nullable=False, index=True)
    reference = db.Column(db.String(100), nullable=False)
    payload = db.Column(db.Text, nullable=False, default='{}')
    version = db.Column(db.Integer, nullable=False, default=1)
    updated_at = db.Column(db.DateTime, nullable=False, default=now, onupdate=now)
    __table_args__ = (db.UniqueConstraint('kind', 'reference'),)

    @property
    def data(self):
        return json.loads(self.payload)


def records(kind):
    return DirectiveRecord.query.filter_by(kind=kind).order_by(DirectiveRecord.id).all()


def lookup(kind, reference='office'):
    return DirectiveRecord.query.filter_by(kind=kind, reference=str(reference)).first()


def settings():
    row = lookup('settings')
    return row.data if row else {}


# All amounts below are whole rials (the source print/online tables use thousands).
PRINT_RATES = ((105000000,63000000,42000000,21000000),
               (73000000,43800000,29200000,14600000),
               (56500000,33900000,22600000,11300000),
               (44000000,26400000,17600000,8800000),
               (33000000,19800000,13200000,6600000))
ONLINE_RATES = (6700000,3300000,1950000)
DEDUCTIONS = {1:10,2:20,3:30,4:30,5:20,6:5,7:20,8:10,9:10,10:20,11:100,12:100,13:10,14:10,15:30}
STATES = ('draft','reviewed','allocated','dispatched','published','verified','certified','paid')
STATE_LABELS = dict(zip(STATES, ('پیش‌نویس','بررسی شده','تخصیص یافته','ارسال رسمی','منتشر شده','تأیید انتشار','صدور گواهی','پرداخت شده')))


def rank(profile):
    score = Decimal(str(profile.get('score') or 0))
    boundaries = (80,65,50) if profile.get('format') in ('website','agency') else (80,60,50,40)
    return next((i+1 for i,bound in enumerate(boundaries) if score >= bound), 3 if len(boundaries)==3 else 5)


def eligibility(profile, on=None):
    on = on or today()
    failures = []
    for key,label in (('license','پروانه انتشار'),('application','درخواست سامانه'),('insurance','بیمه'),('verified','تأیید مدارک')):
        if not profile.get(key): failures.append(label)
    if profile.get('eligible_from') and on < date.fromisoformat(profile['eligible_from']): failures.append('آغاز صلاحیت')
    if profile.get('eligible_until') and on > date.fromisoformat(profile['eligible_until']): failures.append('پایان صلاحیت')
    for event in profile.get('exclusions', []):
        if event['start'] <= on.isoformat() and (not event.get('end') or on.isoformat() <= event['end']): failures.append('محرومیت فعال')
    online = profile.get('format') in ('website','agency')
    score = Decimal(str(profile.get('score') or 0))
    if online:
        if profile.get('hq') == 'تهران':
            if score < 50: failures.append('حداقل ضریب ۵۰')
        elif not profile.get('top_five'): failures.append('فهرست پنج رسانه برتر استان')
    else:
        daily = profile.get('frequency') == 'daily'
        national = profile.get('scope') == 'national'
        deprived = profile.get('deprived')
        minimum = (50 if daily else 70) if national else ((30 if daily else 40) if deprived else (40 if daily else 50))
        overrides = bool(profile.get('threshold_approval'))
        def threshold(key,default):
            permitted = not national or (daily and key=='score')
            value=profile.get('minimum_'+key)
            return Decimal(str(value)) if overrides and permitted and value not in (None,'') else Decimal(str(default))
        for condition,label in ((score >= threshold('score',minimum),'ضریب کیفی'),
            (Decimal(str(profile.get('regularity') or 0)) >= threshold('regularity',50 if deprived and not national else 80),'نظم انتشار'),
            (int(profile.get('pages') or 0) >= threshold('pages',4 if deprived and not national else 8),'تعداد صفحات'),
            (Decimal(str(profile.get('width') or 0)) >= threshold('width',25) and Decimal(str(profile.get('height') or 0)) >= threshold('height',35),'ابعاد صفحات'),
            (not daily or bool(profile.get('website')),'وبگاه روزنامه'),
            ((int(profile.get('provinces') or 0) >= 18) if national else Decimal(str(profile.get('coverage') or 0)) > 60,'گستره توزیع')):
            if not condition: failures.append(label)
    if not profile.get('history_verified'): failures.append('بررسی نظم دو سال گذشته')
    if not profile.get('changed_on'): failures.append('تاریخ مجوز یا تغییر وضعیت')
    changed = profile.get('changed_on')
    if changed and not three_years_elapsed(date.fromisoformat(changed),on):
        if not (score > 70 and profile.get('early_approval')): failures.append('مهلت سه سال')
    if profile.get('specialist') and not profile.get('specialist_approval'): failures.append('درخواست رسانه تخصصی')
    if not profile.get('owner_approval'): failures.append('کنترل محدودیت صاحب امتیاز')
    return failures


def priority(profile, office=None):
    office = office or settings()
    province = office.get('province','خراسان رضوی')
    city = office.get('city','')
    scope, fmt = profile.get('scope'), profile.get('format')
    local_hq = profile.get('hq') == province
    branches = [v.strip() for v in profile.get('branches','').split(',')]
    if scope == 'local' and profile.get('city') != city: return None
    if fmt == 'website': return 1 if local_hq else None
    if fmt == 'agency': return 2 if local_hq or province in branches else None
    if local_hq or province in branches:
        return 1 if scope in ('local','provincial','regional') and local_hq else 2
    return None


def working_days(start, end, config=None):
    config = config or settings()
    holidays = set(config.get('holidays','').split())
    count = 0
    while start < end:
        start += timedelta(days=1)
        if start.weekday() != 4 and start.isoformat() not in holidays: count += 1
    return count


def next_month(value):
    j = jdatetime.date.fromgregorian(date=value)
    year,month = (j.year+1,1) if j.month==12 else (j.year,j.month+1)
    for day in range(j.day,0,-1):
        try: return jdatetime.date(year,month,day).togregorian()
        except ValueError: pass


def quote(data, profile, tariff=None):
    """Pure calculation: no discretionary stacking, no mutation of prior amounts."""
    rank_value = rank(profile)
    for key in ('volume','word_count','character_count','graphic_boxes','area','headline','repeats'):
        value=Decimal(str(data.get(key) or 0))
        if not value.is_finite() or value<0: raise ValueError('مشخصات اندازه و تعداد باید نامنفی و معتبر باشند.')
    volume = Decimal(str(data.get('volume') or 0))
    kind = data.get('billing','government')
    if kind in ('company','civil','criminal'):
        if data.get('character_count'): volume=(Decimal(str(data['character_count']))/600).to_integral_value(rounding='ROUND_CEILING')
        elif data.get('word_count'): volume=(Decimal(str(data['word_count']))/100).to_integral_value(rounding='ROUND_CEILING')
        volume+=Decimal(str(data.get('graphic_boxes') or 0))
    elif data.get('area'): volume=Decimal(str(data['area']))/28
    if volume <= 0: raise ValueError('حجم مثبت وارد کنید.')
    fmt = profile.get('format')
    kind = data.get('billing','government')
    rates = tariff or {'print':PRINT_RATES, 'online':ONLINE_RATES}
    page = int(data.get('page') if data.get('page') not in (None,'') else 3)
    if page not in range(4): raise ValueError('نوع صفحه معتبر نیست.')
    rate = Decimal(str(rates['online'][rank_value-1] if fmt in ('website','agency') else rates['print'][rank_value-1][page]))
    factor = Decimal(1)
    if fmt not in ('website','agency'):
        if kind in ('company','civil'):
            rate = Decimal(6000000 if rank_value <= 2 else 2100000 if kind=='company' else 1050000)
            if rank_value <= 2 and not data.get('named_request'): raise ValueError('درخواست صریح رسانه رتبه یک یا دو الزامی است.')
        elif kind == 'criminal' and data.get('quota_media'): rate = Decimal(2100000)
        else:
            if profile.get('frequency') != 'daily': factor *= Decimal('.4') if rank_value<=2 else Decimal('.5')
            if data.get('monochrome'): factor *= Decimal('.8')
    if data.get('trademark'):
        if kind not in ('company','civil'): raise ValueError('تعرفه علامت تجاری فقط برای ماده ۳ است.')
        factor *= 2
    if kind in ('company','civil','criminal') and data.get('logo'): volume += 1
    if not volume.is_finite(): raise ValueError('حجم معتبر نیست.')
    repeats = int(data.get('repeats') or 1)
    if repeats < 1: raise ValueError('تعداد نوبت مثبت لازم است.')
    base = rate * factor * (1 if fmt in ('website','agency') else volume) * repeats
    headline = Decimal(str(data.get('headline') or (1 if data.get('headline_requested') else 0)))
    if headline and fmt not in ('website','agency'):
        base += Decimal(str(rates['print'][rank_value-1][0])) * factor * headline * repeats
    adjustment = Decimal(str(data.get('adjustment') or 0))
    if adjustment and not data.get('adjustment_approval'): raise ValueError('تأیید تعدیل لازم است.')
    total = base + adjustment
    if total < 0: raise ValueError('مبلغ نهایی منفی است.')
    return {'rank':rank_value,'rate':str(rate),'factor':str(factor),'volume':str(volume), 'base':str(base), 'adjustment':str(adjustment),'total':int(total.quantize(Decimal(1),rounding=ROUND_HALF_UP)), 'schedule':'1403' if tariff is None else tariff.get('id','approved'), 'source':SOURCE}


FIELDS = {
'settings': [('province','استان','text'),('city','شهرستان','text'),('target','هدف اولویت اول (۶۰ تا ۷۰)','number'),('holidays','تعطیلات رسمی؛ تاریخ میلادی جدا با فاصله','textarea'),('calendar_confirmed','تقویم تأیید شده','checkbox'),('tariff_confirmed','نرخ‌نامه ۱۴۰۳ تأیید شده','checkbox'),('authority','مرجع و تاریخ تأیید','text'),('active','فعال‌سازی سیاست دستورالعمل','checkbox')],
'profile': [('name','نام رسانه','text'),('format','قالب','select:daily:چاپی,website:پایگاه خبری,agency:خبرگزاری'),('scope','گستره','select:local:محلی,provincial:استانی,regional:منطقه‌ای,national:سراسری'),('frequency','دوره انتشار','select:daily:روزانه,weekly:هفتگی,other:سایر'),('hq','استان دفتر مرکزی','text'),('city','شهر دفتر مرکزی','text'),('branches','استان دفاتر دارای مجوز؛ جدا با ویرگول انگلیسی','text'),('owner','صاحب امتیاز','text'),('score','ضریب کیفی','number'),('regularity','درصد نظم انتشار','number'),('pages','تعداد صفحات','number'),('width','عرض صفحه (سانتیمتر)','number'),('height','ارتفاع صفحه (سانتیمتر)','number'),('provinces','تعداد استان توزیع','number'),('coverage','درصد پوشش دکه‌ها','number'),('website','نشانی وبگاه','text'),('changed_on','تاریخ مجوز یا آخرین تغییر مشمول انتظار','date'),('eligible_from','آغاز صلاحیت','date'),('eligible_until','پایان صلاحیت','date'),('license','پروانه معتبر بررسی شده','checkbox'),('application','درخواست رسمی سامانه بررسی شده','checkbox'),('insurance','بیمه و کد کارگاه بررسی شده','checkbox'),('top_five','عضو فهرست رسمی پنج رسانه برتر استان','checkbox'),('owner_approval','محدودیت صاحب امتیاز و تحریریه بررسی شده','checkbox'),('deprived','استان کم‌برخوردار با تأیید مرجع','checkbox'),('specialist','تخصصی یا اختصاصی','checkbox'),('specialist_approval','درخواست یا مجوز تخصیص تخصصی','checkbox'),('early_approval','مجوز زودهنگام معاونت','checkbox'),('verified','مدارک تأیید شده','checkbox'),('authority','شماره و تاریخ مستندات رسمی','textarea')],
'order': [('organization','سازمان سفارش‌دهنده','text'),('subject','موضوع مستقل','text'),('representative','نام کارشناس معرفی‌شده','text'),('national_id','کد ملی کارشناس','text'),('mobile','همراه کارشناس','text'),('organization_id','شناسه ملی دستگاه','text'),('economic_id','کد اقتصادی دستگاه','text'),('address','نشانی و کد پستی','textarea'),('official_reference','شناسه رسمی e-rasaneh','text'),('submitted_on','تاریخ ثبت سفارش','date'),('requested_on','اولین تاریخ چاپ درخواستی','date'),('media_count','تعداد رسانه درخواستی','number'),('text','متن آگهی','textarea'),('dispatch_evidence','مرجع و تاریخ ارسال رسمی','text'),('publication_evidence','نشانی دائمی یا مرجع نسخه چاپ','text'),('invoice_on','تاریخ تحویل صورتحساب','date'),('certificate_reference','شماره گواهی رسمی','text'),('certificate_on','تاریخ گواهی','date'),('payment_reference','مرجع پرداخت','text'),('paid_on','تاریخ پرداخت','date'),('decision','تصمیم و مستند مرجع برای استثنا','textarea')],
'placement': [('order_id','شناسه سفارش داخلی','number'),('ad_id','شناسه داخلی آگهی موجود','number'),('outlet_id','شناسه رسانه','number'),('volume','کادر واقعی','number'),('page','صفحه: ۰ اول، ۱ آخر، ۲ دوم/سوم، ۳ داخلی','number'),('repeats','تعداد نوبت','number'),('billing','گروه تعرفه','select:government:دولتی,company:ثبت شرکت و مالکیت صنعتی,civil:سایر حقوقی,criminal:کیفری'),('monochrome','تک‌رنگ','checkbox'),('named_request','درخواست صریح رسانه','checkbox'),('quota_media','متقاضی سهمیه','checkbox'),('logo','آرم (یک کادر ماده ۳/۴)','checkbox'),('trademark','علامت تجاری رنگی','checkbox'),('headline','کادر تیتر صفحه اول','number'),('adjustment','تعدیل تأیید شده (ریال با علامت)','number'),('adjustment_approval','مرجع تأیید تعدیل','text')],
'sanction': [('outlet_id','شناسه رسانه','number'),('article','ماده مستند','text'),('start','آغاز محرومیت','date'),('end','پایان محرومیت (خالی تا ارائه مدارک)','date'),('authority','مرجع و شماره تصمیم','text'),('deduction','کسر سهم ریالی آگهی مستقیم','number')],
}

FIELDS['profile'] += [('license_number','شماره پروانه','text'),('insurance_code','کد کارگاه بیمه','text'),('history_verified','نظم انتشار دو سال گذشته بررسی شده','checkbox')]
FIELDS['profile'] += [('threshold_approval','مرجع و مهلت استثنای حدنصاب با تأیید وزیر','text')] + [('minimum_'+key,'حدنصاب مصوب '+label,'number') for key,label in [('score','ضریب کیفی'),('regularity','نظم انتشار'),('pages','تعداد صفحات'),('width','عرض صفحه'),('height','ارتفاع صفحه')]]
FIELDS['order'] += [('supplement','درخواست ویژه‌نامه استانی یا شهرستانی','checkbox'),('headline_only','صرفاً تیتر و ارجاع به متن','checkbox'),('full_publication_reference','مرجع انتشار کامل نوبت اول','text')]
FIELDS['placement'] += [('headline_requested','تیتر درخواست شده بدون تعیین کادر (پیش‌فرض یک)','checkbox'),('word_count','تعداد کلمات (مواد ۳ و ۴)','number'),('character_count','تعداد نویسه‌ها (مواد ۳ و ۴)','number'),('graphic_boxes','کادر گرافیکی معادل','number'),('area','مساحت نشریه مجلد (سانتیمتر مربع)','number')]
FIELDS['placement'] += [('actual_volume','کادر واقعی منتشر شده','number'),('actual_page','صفحه واقعی (۰ تا ۳)','number'),('actual_on','تاریخ انتشار واقعی','date'),('actual_headline','کادر واقعی تیتر','number'),('publication_evidence','نشانی دائمی یا مرجع PDF','text')]

FIELDS['tariff'] = [('title','عنوان و سال نرخ‌نامه','text'),('effective_on','تاریخ ابلاغ','date'),('authority','شماره و مرجع ابلاغ','text'),('approved','تأیید برای محاسبه','checkbox')] + [
    (f'print_{r}_{p}',f'رتبه {r+1}، صفحه {("اول","آخر","دوم/سوم","داخلی")[p]} (ریال)','number') for r in range(5) for p in range(4)] + [(f'online_{r}',f'برخط رتبه {r+1} (ریال)','number') for r in range(3)]
FIELDS['finding'] = [('outlet_id','شناسه رسانه','number'),('order_id','شناسه سفارش','number'),('occurred_on','تاریخ مغایرت','date'),('category','ردیف جدول ماده ۲۴ (۱ تا ۲۰)','number'),('percent','درصد تأیید شده','number'),('authority','مرجع تشخیص و مستند','text'),('approved','تشخیص رسمی تأیید شده','checkbox')]
FIELDS['obligation'] = [('organization','سازمان یا رسانه مسئول','text'),('article','ماده مستند','text'),('due_on','سررسید','date'),('completed_on','تاریخ انجام','date'),('evidence','مرجع انجام، مکاتبه یا بایگانی','textarea'),('credit_blocked','ممنوعیت سفارش اعتباری با تصمیم مرجع','checkbox'),('authority','مرجع و شماره تصمیم','text')]


bp = Blueprint('directive',__name__)

@bp.route('/admin/directive',methods=['GET','POST'])
def workspace():
    if not g.user: return redirect(url_for('web.login'))
    kind = request.values.get('kind','settings')
    if kind not in FIELDS: abort(404)
    if kind in ('settings','profile','sanction','tariff','finding','obligation') and g.user.role != 'admin': abort(403)
    reference = request.values.get('reference','office' if kind=='settings' else '')
    row = lookup(kind,reference) if reference else None
    data = row.data if row else {}
    if request.method == 'POST':
        try:
            if row and integer(request.form.get('version'),1) != row.version: raise ValueError('اطلاعات تغییر کرده؛ صفحه را تازه کنید.')
            if kind=='order' and data.get('state') == 'paid': raise ValueError('گواهی ثبت شده تغییرپذیر نیست؛ اصلاحیه مستقل ثبت کنید.')
            before = data.copy()
            if kind=='placement' and row:
                parent=db.session.get(DirectiveRecord,integer(before.get('order_id'),1))
                if parent and parent.data.get('state','draft') in ('verified','certified','paid'): raise ValueError('انتشار تأیید شده تغییرپذیر نیست.')
            data = {key:(request.form.get(key)=='1' if typ=='checkbox' else request.form.get(key,'').strip()) for key,label,typ in FIELDS[kind]}
            for key,label,typ in FIELDS[kind]:
                if len(str(data[key]))>30000: raise ValueError('متن طولانی است.')
                if typ.startswith('select:') and data[key] not in [choice.split(':')[0] for choice in typ[7:].split(',')]: raise ValueError('گزینه معتبر انتخاب کنید.')
                if typ=='date' and data[key]: data[key] = parse_date(data[key]).isoformat()
                if typ=='number' and data[key]:
                    value = Decimal(data[key])
                    if not value.is_finite(): raise ValueError('عدد معتبر وارد کنید.')
            if kind=='tariff':
                if row: raise ValueError('نرخ‌نامه قبلی تغییرپذیر نیست؛ نسخه جدید ثبت کنید.')
                if not data['authority'] or not data['effective_on']: raise ValueError('مرجع و تاریخ ابلاغ لازم است.')
                for key in data:
                    if key.startswith(('print_','online_')): data[key]=integer(data[key],1)
            if kind=='finding':
                category=integer(data['category'],1,20)
                percent=Decimal(data['percent'] or 0)
                if category in DEDUCTIONS and percent!=DEDUCTIONS[category]: raise ValueError('درصد با جدول ماده ۲۴ تطابق ندارد.')
                if category in (17,18,19) and not 0<=percent<=(10 if category==17 else 20): raise ValueError('درصد هزینه جانبی بیش از حد مجاز است.')
                if data['approved'] and not data['authority']: raise ValueError('مستند تشخیص لازم است.')
                db.get_or_404(Outlet,integer(data['outlet_id'],1))
                if data['approved'] and category<=15 and data['occurred_on']:
                    limit=date.fromisoformat(data['occurred_on'])-timedelta(days=365)
                    previous=[r for r in records('finding') if r.reference!=reference and r.data.get('outlet_id')==data['outlet_id'] and r.data.get('approved') and int(r.data.get('category') or 0)<=15 and limit.isoformat()<=r.data.get('occurred_on','')<=data['occurred_on']]
                    if len(previous)>=2:
                        profile_record=lookup('profile',data['outlet_id'])
                        if profile_record:
                            p=profile_record.data
                            end=next_month(next_month(date.fromisoformat(data['occurred_on'])))
                            p.setdefault('exclusions',[]).append({'start':data['occurred_on'],'end':end.isoformat(),'authority':data['authority']})
                            profile_record.payload=json.dumps(p,ensure_ascii=False); profile_record.version+=1
            if kind=='settings':
                if not Decimal(data.get('target') or 65).is_finite() or Decimal(data.get('target') or 65)!=int(Decimal(data.get('target') or 65)) or not 60 <= Decimal(data.get('target') or 65) <= 70: raise ValueError('هدف باید بین ۶۰ و ۷۰ باشد.')
                if before.get('active') and records('allocation') and any(before.get(k)!=data.get(k) for k in ('province','city')): raise ValueError('دفتر دارای سابقه تخصیص قابل تغییر نیست.')
                for holiday in data['holidays'].split(): date.fromisoformat(holiday)
                if data['active'] and not all(data[k] for k in ('province','city','calendar_confirmed','tariff_confirmed','authority')): raise ValueError('تنظیمات و تأییدهای دفتر را تکمیل کنید.')
                if data['active']:
                    for ad in Advertisement.query.filter(Advertisement.voided_at.is_(None)).all():
                        if ad.period.year != jdatetime.date.fromgregorian(date=today()).year: continue
                        profile_record=lookup('profile',ad.outlet_id)
                        if not profile_record or priority(profile_record.data,data) is None: raise ValueError('طبقه‌بندی اولویت تمام سوابق سال جاری را تکمیل کنید.')
                        freeze_priority(ad,profile_record.data,data)
            if kind=='profile':
                for key in ('score','regularity','coverage'):
                    if data.get(key) and not 0<=Decimal(data[key])<=100: raise ValueError('درصد و ضریب باید بین صفر و صد باشند.')
                for key in ('score','regularity','pages','width','height'):
                    value=data.get('minimum_'+key)
                    if value and (not data['threshold_approval'] or Decimal(value)<0): raise ValueError('حدنصاب استثنایی نیازمند مقدار نامنفی و تأیید وزیر است.')
                db.get_or_404(Outlet,integer(reference,1))
                if data['verified'] and not all(data.get(k) for k in ('authority','owner','changed_on','history_verified')): raise ValueError('مستند رسمی تأیید الزامی است.')
                data['exclusions'] = before.get('exclusions',[])
                if data['top_five'] and data['verified']:
                    others=[r for r in records('profile') if r.reference!=reference and r.data.get('hq')==data['hq'] and r.data.get('top_five') and r.data.get('verified') and r.data.get('format') in ('website','agency')]
                    if len(others)>=5: raise ValueError('حداکثر پنج رسانه برخط در هر استان مجاز است.')
            if kind=='sanction':
                if row: raise ValueError('تصمیم محرومیت ثبت شده تغییرپذیر نیست؛ تصمیم جدید ثبت کنید.')
                if not data['start'] or not data['authority']: raise ValueError('تاریخ و مستند تصمیم لازم است.')
                if data['end'] and data['end'] < data['start']: raise ValueError('بازه محرومیت معتبر نیست.')
                profile = lookup('profile',integer(data['outlet_id'],1))
                if not profile: raise ValueError('ابتدا پرونده رسانه را تکمیل کنید.')
                p = profile.data
                p.setdefault('exclusions',[]).append({'start':data['start'],'end':data['end'],'authority':data['authority']})
                profile.payload=json.dumps(p,ensure_ascii=False); profile.version+=1
            if kind=='order':
                if data['official_reference']:
                    data['official_reference']=str(integer(data['official_reference'],1))
                    if any(r.reference!=reference and r.data.get('official_reference')==data['official_reference'] for r in records('order')): raise ValueError('شناسه رسمی قبلاً برای سفارش دیگری ثبت شده است.')
                state = request.form.get('state','draft')
                old = before.get('state','draft')
                if state not in STATES or STATES.index(state) not in (STATES.index(old),STATES.index(old)+1): raise ValueError('مراحل گردش‌کار را به ترتیب تکمیل کنید.')
                if state in ('verified','certified','paid') and g.user.role!='admin': abort(403)
                if data['supplement']: raise ValueError('آگهی دولتی در ویژه‌نامه استانی یا شهرستانی ممنوع است.')
                if data['headline_only'] and not data['full_publication_reference']: raise ValueError('مرجع انتشار کامل نوبت اول لازم است.')
                if state!='draft' and not all(data[k] for k in ('organization','subject','representative','text','submitted_on','requested_on')): raise ValueError('اطلاعات اصلی سفارش الزامی است.')
                if state=='reviewed' and working_days(date.fromisoformat(data['submitted_on']),date.fromisoformat(data['requested_on'])) < 3 and not data['decision']: raise ValueError('حداقل سه روز کاری تا چاپ یا تصمیم مستند لازم است.')
                placements = [p for p in records('placement') if p.data.get('order_id')==str(row.id if row else '')]
                if state=='reviewed':
                    from pathlib import Path
                    word=any(a.get('file_mime') in ('application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document') for a in before.get('attachments',[]))
                    incoming=request.files.get('evidence')
                    if not word and (not incoming or Path(incoming.filename).suffix.lower() not in ('.doc','.docx')): raise ValueError('فایل Word سفارش را بارگذاری کنید.')
                if state=='allocated' and old!=state:
                    if not placements: raise ValueError('حداقل یک انتشار ثبت کنید.')
                    allocate_order(data,placements)
                required = {'dispatched':('official_reference','dispatch_evidence'),'published':('publication_evidence',),'verified':('publication_evidence','invoice_on'),'certified':('certificate_reference','certificate_on'),'paid':('payment_reference','paid_on')}
                if any(not data[k] for k in required.get(state,())): raise ValueError('مستندات این مرحله را تکمیل کنید.')
                if state=='verified':
                    if any(not all(p.data.get(k) for k in ('actual_volume','actual_on','publication_evidence')) for p in placements): raise ValueError('مشخصات واقعی و مدرک تمام انتشارها را تکمیل کنید.')
                if state=='certified':
                    if not placements: raise ValueError('انتشار برای گواهی موجود نیست.')
                    data['certificate']={'placements':[p.data for p in placements],'total':sum(p.data['quote']['total'] for p in placements),'issued_at':now().isoformat()}
                    data['due_on']=next_month(date.fromisoformat(data['certificate_on'])).isoformat()
                else:
                    for key in ('certificate','due_on'):
                        if key in before: data[key]=before[key]
                if old=='certified':
                    for key in before:
                        if key not in ('state','payment_reference','paid_on','attachments') and data.get(key)!=before[key]: raise ValueError('گواهی تغییرپذیر نیست.')
                data['state']=state
            if kind=='placement':
                if not settings().get('tariff_confirmed'): raise ValueError('ابتدا نرخ‌نامه را تأیید کنید.')
                order = lookup('order',reference='missing') if not data['order_id'] else db.session.get(DirectiveRecord,integer(data['order_id'],1))
                if not order or order.kind!='order' or order.data.get('state','draft') not in ('draft','reviewed','allocated','dispatched','published'): raise ValueError('سفارش باید پیش از تخصیص باشد.')
                profile=lookup('profile',integer(data['outlet_id'],1))
                if not profile or eligibility(profile.data) or priority(profile.data) is None: raise ValueError('رسانه واجد شرایط و گستره دفتر نیست.')
                if data['ad_id']:
                    ad=db.get_or_404(Advertisement,integer(data['ad_id'],1))
                    if ad.outlet_id != int(data['outlet_id']): raise ValueError('رسانه آگهی مغایرت دارد.')
                if before and order.data.get('state','draft') not in ('draft','reviewed'):
                    for key in ('order_id','ad_id','outlet_id','volume','page','repeats','billing','monochrome','named_request','quota_media','logo','trademark','headline'):
                        if data.get(key)!=before.get(key): raise ValueError('مشخصات سفارش پس از تخصیص تغییرپذیر نیست.')
                actual=dict(data)
                for key in ('volume','page','headline'):
                    if data.get('actual_'+key) not in ('',None): actual[key]=data['actual_'+key]
                tariff_date=date.fromisoformat(data['actual_on']) if data['actual_on'] else today()
                data['quote']=quote(actual,profile.data,approved_tariff(tariff_date))
                if 'ad_ids' in before: data['ad_ids']=before['ad_ids']
            data['attachments']=before.get('attachments',[])
            upload=request.files.get('evidence')
            if upload and upload.filename:
                if kind=='order' and before.get('state') in ('certified','paid'): raise ValueError('مستندات گواهی تغییرپذیر نیست.')
                data['attachments']=data['attachments']+[store_evidence(upload)]
            if not row:
                row=DirectiveRecord(kind=kind,reference=reference or __import__('secrets').token_hex(12))
                db.session.add(row)
            else: row.version+=1
            row.payload=json.dumps(data,ensure_ascii=False)
            for period in Period.query.all(): period.revision+=1
            audit('directive_'+kind,reference or 'new',{'before':before,'after':data})
            db.session.commit()
            g.staged_files=[]
            flash('اطلاعات و مستند تصمیم ذخیره شد.','success')
            return redirect(url_for('directive.workspace',kind=kind,reference=row.reference))
        except (ValueError,ArithmeticError) as error:
            db.session.rollback()
            for path in g.staged_files: path.unlink(missing_ok=True)
            g.staged_files=[]
            flash(str(error),'danger')
    return render_template('admin/directive.html',kind=kind,fields=FIELDS[kind],data=data,row=row,
                           rows=records(kind),outlets=Outlet.query.all(),states=STATE_LABELS,source=SOURCE,
                           failures=eligibility(data) if kind=='profile' and row else [],reference=reference)


def policy_state(state):
    """Apply verified provincial priorities without rewriting historical memberships."""
    config=settings()
    if not config.get('active'): return state
    from .allocations import group_stats
    profiles={int(p.reference):p for p in records('profile')}
    totals={'local':0,'national':0}
    unknown=0
    for row in state['rows']:
        record=profiles.get(row['outlet_id'])
        p=record.data if record else {}
        group=priority(p,config) if record else None
        row['priority']=group
        row['eligibility_failures']=eligibility(p) if record else ['پرونده تأیید نشده']
        row['active']=row['active'] and bool(group) and not row['eligibility_failures']
        row['profile_version']=record.version if record else 0
        row['profile']=p
        if group:
            row['category']='local' if group==1 else 'national'
            totals[row['category']]+=row['allocated_units']
        else: unknown+=row['allocated_units']
    frozen={r.reference:r.data for r in records('allocation')}
    if frozen:
        totals={'local':0,'national':0}; unknown=0
        ads=Advertisement.query.filter_by(period_id=state['period'].id).filter(Advertisement.voided_at.is_(None)).all() if state['period'] else []
        for ad in ads:
            if ad.id==state.get('excluded_id'): continue
            saved=frozen.get(str(ad.id))
            current=profiles.get(ad.outlet_id)
            value=saved.get('priority') if saved else priority(current.data,config) if current else None
            if value: totals['local' if value==1 else 'national']+=ad.volume_units or 0
            else: unknown+=ad.volume_units or 0
    state['categories']=totals
    state['local_units']=totals['local']
    state['unknown_units']=unknown
    state['directive']=True
    state['policy_config']=config
    state['groups']={key:group_stats(key,amount,state['total_units'],int(config.get('target') or 65) if key=='local' else 100-int(config.get('target') or 65)) for key,amount in totals.items()}
    target=int(config.get('target') or 65)
    for key,group in state['groups'].items():
        percent=target if key=='local' else 100-target
        group.update(target_percent=percent,expected_units=Fraction(state['total_units']*percent,100))
        from .services import quota_boxes,decimal_string
        group['expected']=quota_boxes(group['expected_units'])
        group['exceeded']=group['allocated_units']>group['expected_units']
        group['media_count']=sum(r['category']==key for r in state['rows'])
        group['active_count']=sum(r['category']==key and r['active'] for r in state['rows'])
    from .services import decimal_string
    state['local_percent']=decimal_string(Fraction(totals['local']*100,state['total_units'])) if state['total_units'] else '0'
    state['national_percent']=decimal_string(Fraction(totals['national']*100,state['total_units'])) if state['total_units'] else '0'
    return state


def normalized_volume(row,period):
    """Monthly entitlement is prorated by eligible days within each Persian month."""
    profile=row.get('profile',{})
    start,end=period_bounds(period.year)
    end=min(end,today()+timedelta(days=1))
    eligible_months=Fraction(0)
    cursor=start
    while cursor<end:
        j=jdatetime.date.fromgregorian(date=cursor)
        year,month=(j.year+1,1) if j.month==12 else (j.year,j.month+1)
        boundary=jdatetime.date(year,month,1).togregorian()
        days=(boundary-cursor).days
        valid=0
        for offset in range((min(boundary,end)-cursor).days):
            d=cursor+timedelta(days=offset)
            if profile.get('eligible_from') and d.isoformat()<profile['eligible_from']: continue
            if profile.get('eligible_until') and d.isoformat()>profile['eligible_until']: continue
            if any(e['start']<=d.isoformat() and (not e.get('end') or d.isoformat()<=e['end']) for e in profile.get('exclusions',[])): continue
            valid+=1
        eligible_months+=Fraction(valid,days)
        cursor=boundary
    weight=Fraction(13,10) if profile.get('scope')=='national' and profile.get('hq')==settings().get('province') else Fraction(1)
    return Fraction(row['allocated_units'],1)/max(eligible_months,Fraction(1,31))/weight


def directive_recommend(state,volume):
    config=state['policy_config']
    rows=[r for r in state['rows'] if r['active']]
    target=Fraction(int(config.get('target') or 65),100)
    def score(row):
        local=state['local_units']+(volume if row['priority']==1 else 0)
        share=Fraction(local,state['total_units']+volume) if state['total_units']+volume else target
        outside=max(Fraction(0),Fraction(60,100)-share,share-Fraction(70,100))
        return (outside,abs(share-target),normalized_volume(row,state['period']),row['last'] or 0,row['outlet_id'])
    return min(rows,key=score) if rows else None


def allocation_findings(state,selected,volume):
    findings=[]
    if state.get('unknown_units'): findings.append('سوابق دارای اولویت نامشخص است؛ طبقه‌بندی سال جاری را تکمیل کنید.')
    if not selected: return findings
    profile=selected.get('profile',{})
    peers=[r for r in state['rows'] if r['active'] and r.get('priority')==selected.get('priority') and
           r.get('profile',{}).get('format')==profile.get('format') and
           r.get('profile',{}).get('scope')==profile.get('scope') and
           r.get('profile',{}).get('frequency')==profile.get('frequency')]
    online=profile.get('format') in ('agency','website')
    if not online: peers=[r for r in peers if rank(r.get('profile',{}))!=1]
    if len(peers)>1:
        amounts=[normalized_volume(dict(r,allocated_units=r['allocated_units']+(volume if r['outlet_id']==selected['outlet_id'] else 0)),state['period']) for r in peers]
        low,high=min(amounts),max(amounts)
        if high>(2 if online else Fraction(6,5))*low: findings.append('اختلاف حجم همتایان از حد ماده ۷ عبور می‌کند؛ تصمیم مستند مدیر لازم است.')
    total=state['total_units']+volume
    local=state['local_units']+(volume if selected.get('priority')==1 else 0)
    if total and not 60*total<=100*local<=70*total: findings.append('سهم اولویت اول خارج از بازه ۶۰ تا ۷۰ درصد است؛ تصمیم مستند مدیر لازم است.')
    return findings



def approved_tariff(on):
    candidates=[r for r in records('tariff') if r.data.get('approved') and r.data.get('effective_on','9999')<=on.isoformat()]
    if not candidates: return None
    record=max(candidates,key=lambda r:(r.data['effective_on'],r.id))
    d=record.data
    return {'print':[[d[f'print_{r}_{p}'] for p in range(4)] for r in range(5)],'online':[d[f'online_{r}'] for r in range(3)],'id':record.id,'authority':d['authority']}



def allocate_order(order, placements):
    """Create real allocation rows in the same serialized order transition transaction."""
    from sqlalchemy import func
    from .models import Organization
    from .services import units
    from .allocations import find_period, preview
    if not settings().get('active'): raise ValueError('ابتدا سیاست دفتر را فعال کنید.')
    allocated=today()
    period=find_period(allocated)
    if not period or not period.active: raise ValueError('دوره فعال برای تخصیص موجود نیست.')
    organization=Organization.query.filter_by(name=order['organization']).first()
    if not organization: raise ValueError('سازمان سفارش‌دهنده را ابتدا در فهرست سازمان‌ها ثبت کنید.')
    if not organization.active: raise ValueError('سازمان فعال نیست.')
    for obligation in records('obligation'):
        if obligation.data.get('organization')==organization.name and obligation.data.get('credit_blocked'):
            raise ValueError('سفارش اعتباری سازمان با تصمیم مرجع متوقف است؛ وضعیت پرداخت را بررسی کنید.')
    for placement in placements:
        data=placement.data
        if data.get('ad_id'):
            ad=db.get_or_404(Advertisement,integer(data['ad_id'],1))
            if ad.voided_at or ad.organization_id!=organization.id: raise ValueError('سابقه آگهی با سفارش تطابق ندارد.')
            continue
        ad_ids=[]
        for occurrence in range(integer(data.get('repeats') or '1',1,1000)):
            volume=units(data['volume'],positive=True)
            result=preview(period,volume,integer(data['outlet_id'],1))
            if data.get('named_request') and any('همتایان' in warning for warning in result['warnings']) and len(placements)<3: raise ValueError('درخواست رسانه خارج از حدود حجم، به دو رسانه دیگر نیز نیاز دارد.')
            if result['warnings'] and (g.user.role!='admin' or not order.get('decision')):
                raise ValueError('تخصیص نیازمند تصمیم مستند مدیر درباره حدود حجم است.')
            reference=max(9000000000000,(db.session.query(func.max(Advertisement.ma_reference)).scalar() or 0)+1)
            ad=Advertisement(ma_reference=reference,organization_id=organization.id,outlet_id=int(data['outlet_id']),
                             period_id=period.id,allocation_date=allocated,volume_units=volume,
                             proposed_date=date.fromisoformat(order['requested_on']),creator_id=g.user.id,
                             subject_description=order['subject'],ad_text=order['text'],
                             notes=f'سفارش داخلی {placement.data["order_id"]}، نوبت {occurrence+1}')
            db.session.add(ad); db.session.flush(); freeze_priority(ad,lookup('profile',ad.outlet_id).data,settings()); ad_ids.append(ad.id)
            allocation=lookup('allocation',ad.id)
            metadata=allocation.data;metadata['order_id']=order.get('_id') or data['order_id'];allocation.payload=json.dumps(metadata)
        data['ad_ids']=ad_ids
        placement.payload=json.dumps(data,ensure_ascii=False);placement.version+=1
    period.revision+=1



def store_evidence(upload):
    import io
    import secrets
    import zipfile
    from pathlib import Path
    from flask import current_app
    from .records import store_attachment
    suffix=Path(upload.filename).suffix.lower()
    if suffix not in ('.doc','.docx'): return store_attachment(upload)
    content=upload.read(10*1024*1024+1)
    if len(content)>10*1024*1024: raise ValueError('حداکثر اندازه فایل ۱۰ مگابایت است.')
    if suffix=='.doc':
        if not content.startswith(bytes.fromhex('D0CF11E0A1B11AE1')): raise ValueError('فایل Word معتبر نیست.')
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(i.file_size for i in archive.infolist())>30*1024*1024: raise ValueError('فایل Word بسیار بزرگ است.')
                if '[Content_Types].xml' not in archive.namelist() or 'word/document.xml' not in archive.namelist(): raise ValueError('فایل Word معتبر نیست.')
                if any('vbaProject' in name for name in archive.namelist()): raise ValueError('فایل دارای ماکرو پذیرفته نمی‌شود.')
        except zipfile.BadZipFile: raise ValueError('فایل Word معتبر نیست.') from None
    key=secrets.token_hex(24)+suffix
    root=Path(current_app.config['STORAGE_ROOT']);root.mkdir(parents=True,exist_ok=True)
    path=root/key;g.staged_files.append(path)
    with path.open('xb') as output: output.write(content)
    name=upload.filename.replace(chr(92),'/').rsplit('/',1)[-1]
    return {'file_key':key,'file_name':''.join(c for c in name if c.isprintable())[:240],
            'file_mime':'application/vnd.openxmlformats-officedocument.wordprocessingml.document' if suffix=='.docx' else 'application/msword'}


@bp.route('/admin/directive/<int:record_id>/evidence/<int:index>')
def evidence(record_id,index):
    from pathlib import Path
    from flask import current_app,send_file
    if not g.user: abort(403)
    record=db.get_or_404(DirectiveRecord,record_id)
    if record.kind not in ('order','placement') and g.user.role!='admin': abort(403)
    attachments=record.data.get('attachments',[])
    if index>=len(attachments): abort(404)
    attachment=attachments[index]
    path=Path(current_app.config['STORAGE_ROOT'])/attachment['file_key']
    if not path.is_file(): abort(404)
    return send_file(path,as_attachment=True,download_name=attachment['file_name'],mimetype=attachment['file_mime'])



def freeze_priority(ad,profile,config):
    existing=lookup('allocation',ad.id)
    if existing and existing.data.get('outlet_id')==ad.outlet_id and existing.data.get('period_id')==ad.period_id: return
    if existing:
        audit('directive_allocation_amend',str(ad.id),{'before':existing.data})
        existing.payload=json.dumps({'priority':priority(profile,config),'outlet_id':ad.outlet_id,'period_id':ad.period_id,'recorded_at':now().isoformat()})
        existing.version+=1
        return
    group=priority(profile,config)
    if group:
        db.session.add(DirectiveRecord(kind='allocation',reference=str(ad.id),payload=json.dumps({
            'priority':group,'outlet_id':ad.outlet_id,'period_id':ad.period_id,'recorded_at':now().isoformat()})))


@bp.route('/reports/directive-quarter')
def quarter_report():
    from .services import period_key
    from .web import spreadsheet
    try:
        year=integer(request.args.get('year',str(period_key())),1200,1600)
        quarter=integer(request.args.get('quarter','1'),1,4)
    except ValueError as error:
        abort(400,str(error))
    month=(quarter-1)*3+1
    start=jdatetime.date(year,month,1).togregorian()
    end=jdatetime.date(year+1,1,1).togregorian() if quarter==4 else jdatetime.date(year,month+3,1).togregorian()
    from sqlalchemy import func
    rows=db.session.query(Outlet.name,func.sum(Advertisement.volume_units),func.count(Advertisement.id)).join(Advertisement,Advertisement.outlet_id==Outlet.id).filter(
        Advertisement.voided_at.is_(None),Advertisement.allocation_date>=start,Advertisement.allocation_date<end).group_by(Outlet.id).order_by(Outlet.name).all()
    return spreadsheet(['رسانه','حجم کادر','تعداد آگهی'],[(name,float(Fraction(volume or 0,100)),count) for name,volume,count in rows])



def three_years_elapsed(start,on):
    j=jdatetime.date.fromgregorian(date=start)
    try: anniversary=jdatetime.date(j.year+3,j.month,j.day).togregorian()
    except ValueError: anniversary=jdatetime.date(j.year+3,j.month,29).togregorian()
    return on>=anniversary


@bp.route('/api/tariff-preview')
def tariff_preview():
    from flask import jsonify
    if not g.user: abort(403)
    try:
        record=lookup('profile',integer(request.args.get('outlet_id'),1))
        if not record or eligibility(record.data): raise ValueError('رسانه واجد شرایط انتخاب کنید.')
        data=request.args.to_dict()
        for key,label,typ in FIELDS['placement']:
            if typ=='checkbox': data[key]=data.get(key)=='1'
        effective=parse_date(data.get('actual_on') or today().isoformat())
        return jsonify(quote(data,record.data,approved_tariff(effective)))
    except (ValueError,ArithmeticError) as error:
        return jsonify(error=str(error)),400


@bp.route('/admin/directive/<int:record_id>/certificate')
def certificate(record_id):
    if not g.user or g.user.role!='admin': abort(403)
    record=db.get_or_404(DirectiveRecord,record_id)
    if record.kind!='order' or not record.data.get('certificate'): abort(404)
    return render_template('admin/directive-certificate.html',data=record.data,source=SOURCE)



def official_reference(ad):
    allocation=lookup('allocation',ad.id)
    if allocation and allocation.data.get('order_id'):
        order=db.session.get(DirectiveRecord,int(allocation.data['order_id']))
        if order: return order.data.get('official_reference') or '—'
    return '—'

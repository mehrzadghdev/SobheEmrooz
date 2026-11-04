"""Stable codes and Persian labels shared by forms, exports, and validation."""
CATEGORIES = ('local', 'national')
CATEGORY_LABELS = {'local': 'رسانه‌های محلی', 'national': 'رسانه‌های سراسری'}
WEIGHTS = {'local': 50, 'national': 50}
ROLES = {'admin': 'مدیر سامانه', 'operator': 'کارشناس تخصیص'}
AD_TYPES = ('ثبت شرکت', 'ثبت کل', 'دادگستری - حقوقی', 'دادگستری - کیفری', 'دولتی، سفارشی', 'متفرقه')
PRINT_STATUSES = ('منتظر چاپ', 'چاپ شده', 'عدم چاپ', 'تایید چاپ شده', 'عدم تایید چاپ شده',
                  'اقدام برای چاپ', 'چاپ صدور گواهی', 'لغو تایید آگهی')
PROVINCES = ('آذربایجان شرقی', 'آذربایجان غربی', 'اردبیل', 'اصفهان', 'البرز', 'ایلام', 'بوشهر', 'تهران',
             'چهارمحال و بختیاری', 'خراسان جنوبی', 'خراسان رضوی', 'خراسان شمالی', 'خوزستان', 'زنجان',
             'سمنان', 'سیستان و بلوچستان', 'فارس', 'قزوین', 'قم', 'کردستان', 'کرمان', 'کرمانشاه',
             'کهگیلویه و بویراحمد', 'گلستان', 'گیلان', 'لرستان', 'مازندران', 'مرکزی', 'هرمزگان', 'همدان', 'یزد')
DATE_FIELDS = {'approval_date': 'تاریخ تایید آگهی', 'allocation_date': 'تاریخ تخصیص',
               'created_at': 'تاریخ ایجاد آگهی', 'proposed_date': 'تاریخ پیشنهادی',
               'final_print_date': 'تاریخ چاپ نهایی', 'letter_date': 'تاریخ نامه سازمان'}
# Ordering here is the ordering of selectable and exported columns.
COLUMNS = {
    'id': 'شناسه داخلی', 'subject': 'موضوع', 'subject_description': 'شرح موضوع',
    'ma_reference': 'شناسه آگهی', 'organization': 'سازمان آگهی‌دهنده', 'outlet': 'رسانه',
    'print_status': 'وضعیت چاپ', 'proposed_date': 'تاریخ پیشنهادی', 'final_print_date': 'تاریخ چاپ نهایی',
    'letter_number': 'شماره نامه آگهی‌دهنده', 'ad_text': 'متن آگهی', 'page_number': 'شماره صفحه',
    'box_size': 'اندازه کادر', 'headline_units': 'کادر تیتر صفحه اول', 'volume_units': 'حجم آگهی',
    'approval_date': 'تاریخ تایید آگهی', 'province': 'استان', 'file': 'فایل آگهی',
    'amount': 'مبلغ آگهی (ریال)', 'ad_type': 'نوع آگهی', 'created_at': 'تاریخ ایجاد آگهی',
    'page_type': 'نوع صفحه', 'notes': 'توضیحات آگهی', 'distribution_location': 'محل توزیع',
    'letter_date': 'تاریخ نامه سازمان', 'approved_units': 'حجم تایید شده', 'approver_name': 'فرد تاییدکننده',
    'approved_amount': 'مبلغ تایید شده (ریال)', 'allocation_date': 'تاریخ تخصیص',
}
DEFAULT_COLUMNS = ('id', 'ma_reference', 'subject', 'organization', 'outlet', 'volume_units', 'allocation_date',
                   'approval_date', 'print_status', 'amount')
TEXT_FIELDS = {'subject_description': 1000, 'letter_number': 100, 'ad_text': 30000, 'box_size': 100,
               'page_type': 100, 'notes': 5000, 'distribution_location': 500, 'approver_name': 150}

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

from openpyxl import load_workbook

from sobhe import allocations as a
from sobhe import records as r
from sobhe.models import *
from tests.support import AppTestCase, png_bytes


class AdvertisementTests(AppTestCase):
    def test_manual_allocation_persists_and_changes_recommendations(self):
        result = self.preview()
        response = self.submit()
        self.assertEqual(response.status_code, 302)
        ad = Advertisement.query.one()
        self.assertEqual(ad.volume_units, 1000)
        self.assertEqual(ad.creator_id, self.operator.id)
        self.assertEqual(a.snapshot(self.period)['total'], '10')
        self.assertEqual(self.preview()['recommended']['category'], 'national')
        event = AuditEvent.query.filter_by(action='advertisement_create').one()
        self.assertTrue(json.loads(event.detail)['override_confirmed'])
        self.assertEqual(event.actor_id, self.operator.id)

    def test_warning_requires_acknowledgement(self):
        response = self.submit(self.data(override_confirmed=''))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Advertisement.query.count(), 0)

    def test_missing_forged_and_stale_preview_tokens_do_not_save(self):
        response = self.client.post('/advertisements/new', data=self.data(preview_token='forged'))
        self.assertEqual(response.status_code, 409)
        data = self.data()
        data['preview_token'] = self.preview(data)['token']
        self.add_ad(4, 10)
        response = self.client.post('/advertisements/new', data=data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Advertisement.query.count(), 1)

    def test_duplicate_ma_normalizes_persian_digits_and_leading_zeros(self):
        self.add_ad(ma_reference=1002)
        response = self.submit(self.data(ma_reference='۰۰۱۰۰۲'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Advertisement.query.count(), 1)

    def test_all_optional_fields_and_public_attachment_round_trip(self):
        data = self.data(volume='۲٫۵۰', ma_reference='۱۰۰۲', subject_id=str(Subject.query.first().id),
                         province='خراسان رضوی', ad_type='دولتی، سفارشی', letter_number='۱۴۰۵/۱۲۳',
                         letter_date='1405/07/01', proposed_date='2026-10-02', final_print_date='2026-10-03',
                         approval_date='2026-10-01', page_number='4', page_type='داخلی', box_size='۲×۵',
                         headline_volume='0.25', approved_volume='99.99', approver_name='نام تاییدکننده',
                         amount='۱۲۰۰۰۰۰', approved_amount='۰', ad_text='متن عمومی آگهی', notes='توضیحات',
                         subject_description='شرح موضوع', distribution_location='مشهد')
        data['preview_token'] = self.preview(data)['token']
        data['file'] = (io.BytesIO(png_bytes()), 'آگهی.png')
        response = self.client.post('/advertisements/new', data=data)
        self.assertEqual(response.status_code, 302)
        ad = Advertisement.query.one()
        self.assertEqual(ad.volume_units, 250)
        self.assertEqual(ad.approved_units, 9999)
        self.assertEqual(ad.amount, 1200000)
        self.assertEqual(ad.approved_amount, 0)
        public = self.app.test_client()
        attachment = public.get(f'/advertisements/{ad.id}/file')
        self.assertEqual(attachment.status_code, 200)
        self.assertEqual(attachment.data, png_bytes())
        self.assertIn('attachment;', attachment.headers['Content-Disposition'])
        attachment.close()
        self.assertEqual(a.snapshot(self.period)['total'], '2.5', 'Approved volume must not affect allocations')
        detail = public.get(f'/advertisements/{ad.id}')
        self.assertIn('متن عمومی آگهی', detail.get_data(as_text=True))

    def test_invalid_file_rolls_back_record_and_does_not_leave_files(self):
        data = self.data()
        data['preview_token'] = self.preview(data)['token']
        data['file'] = (io.BytesIO(b'<script>bad</script>'), 'bad.png')
        response = self.client.post('/advertisements/new', data=data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Advertisement.query.count(), 0)
        self.assertFalse(list((self.root / 'storage').glob('*')))

    def test_zero_negative_and_overprecision_volumes_are_rejected(self):
        for value in ('0', '-1', '0.001', 'NaN', 'inf'):
            response = self.client.get('/api/allocation-preview', query_string=self.data(volume=value))
            self.assertEqual(response.status_code, 400)

    def test_admin_correction_moves_volume_between_periods(self):
        other = self.make_period(1405, 1)
        ad = self.add_ad(0, 10)
        self.sign_in(admin=True)
        data = r.form_values(ad)
        data.update(volume='20.25', allocation_date='2026-04-01', override_confirmed='1')
        response = self.submit(data, ad)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(a.snapshot(self.period)['total'], '0')
        self.assertEqual(a.snapshot(other)['total'], '20.25')
        self.assertEqual(ad.version, 2)
        self.assertEqual(AuditEvent.query.filter_by(action='advertisement_edit').count(), 1)

    def test_editing_retired_outlet_keeps_historical_record_editable(self):
        ad = self.add_ad()
        self.outlets[0].active = False
        db.session.commit()
        self.sign_in(admin=True)
        data = r.form_values(ad)
        data.update(notes='اصلاح توضیح تاریخی', override_confirmed='1')
        response = self.submit(data, ad)
        self.assertEqual(response.status_code, 302)

    def test_void_keeps_ma_reserved_and_removes_public_record_and_quota(self):
        ad = self.add_ad(ma_reference=1002)
        self.sign_in(admin=True)
        response = self.client.post(f'/advertisements/{ad.id}/void', data={'reason':'ثبت اشتباه', 'version':str(ad.version)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(a.snapshot(self.period)['total'], '0')
        self.assertIsNotNone(ad.voided_at)
        self.assertEqual(self.app.test_client().get(f'/advertisements/{ad.id}').status_code, 404)
        self.assertEqual(self.submit(self.data()).status_code, 400)

    def test_print_cancellation_status_does_not_void_allocation(self):
        self.add_ad(print_status='لغو تایید آگهی')
        self.assertEqual(a.snapshot(self.period)['total'], '10')

    def test_concurrent_identical_ma_is_recorded_once(self):
        admin_id, operator_id = self.admin.id, self.operator.id
        data = self.data()
        data['preview_token'] = self.preview(data)['token']
        barrier = Barrier(2)
        def submit():
            client = self.app.test_client()
            with client.session_transaction() as session:
                session['user_id'], session['auth_version'] = operator_id, 1
            barrier.wait(timeout=5)
            return client.post('/advertisements/new', data=data).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))
        self.assertEqual(sorted(results), [302, 400])
        db.session.expire_all()
        self.assertEqual(Advertisement.query.count(), 1)


class AccessTests(AppTestCase):
    def test_reports_and_details_are_public_but_writes_require_login(self):
        ad = self.add_ad(approval_date=datetime(2026,10,1).date())
        client = self.app.test_client()
        for path in ('/', '/reports?year=1405&half=2', '/reports?view=media', f'/advertisements/{ad.id}'):
            self.assertEqual(client.get(path).status_code, 200)
        self.assertEqual(client.post('/advertisements/new', data=self.data()).status_code, 302)
        self.assertEqual(client.get('/api/allocation-preview').status_code, 401)

    def test_operator_cannot_manage_users_targets_or_edit_void_records(self):
        ad = self.add_ad()
        for path in ('/admin/users', '/admin/periods', '/admin/catalog/outlets', '/admin/audit',
                     f'/advertisements/{ad.id}/edit'):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post(f'/advertisements/{ad.id}/void').status_code, 403)

    def test_deactivation_and_password_reset_invalidate_sessions(self):
        self.operator.active = False
        db.session.commit()
        self.assertEqual(self.client.get('/advertisements/new').status_code, 302)
        self.operator.active = True
        db.session.commit()
        self.sign_in()
        self.operator.auth_version += 1
        db.session.commit()
        self.assertEqual(self.client.get('/advertisements/new').status_code, 302)

    def test_csrf_login_logout_and_writes_are_protected(self):
        self.app.config['CSRF_ENABLED'] = True
        self.assertEqual(self.client.post('/advertisements/new', data=self.data()).status_code, 400)
        client = self.app.test_client()
        client.get('/login')
        with client.session_transaction() as session:
            token = session['_csrf']
        self.assertEqual(client.post('/login', data={'username':'operator','password':'test-password-123','csrf_token':token}).status_code, 302)
        self.assertEqual(client.get('/logout').status_code, 405)
        self.assertEqual(client.post('/logout').status_code, 400)

    def test_last_active_admin_cannot_be_deactivated(self):
        self.sign_in(admin=True)
        response = self.client.post('/admin/users', data={'id':self.admin.id, 'username':self.admin.username,
                                    'name':self.admin.name,'role':'operator','active':'1'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.admin.role, 'admin')

    def test_all_authorized_pages_render(self):
        self.sign_in(admin=True)
        ad = self.add_ad()
        for path in ('/advertisements/new', f'/advertisements/{ad.id}/edit', '/admin/catalog/outlets',
                     '/admin/catalog/organizations','/admin/catalog/subjects','/admin/periods',
                     f'/admin/periods/{self.period.id}', '/admin/users', '/admin/audit'):
            self.assertEqual(self.client.get(path).status_code, 200, path)


class ReportTests(AppTestCase):
    def test_reference_filters_and_selected_columns(self):
        self.add_ad(ma_reference=1002, approval_date=datetime(2026,10,1).date(), province='خراسان رضوی',
                    subject_description='مناقصه عمرانی', letter_number='ABC/12', ad_text='ساخت پل',
                    amount=5000, approved_amount=4000, ad_type='دولتی، سفارشی', print_status='چاپ شده')
        self.add_ad(ma_reference=1003, approval_date=datetime(2026,10,1).date(), organization_id=self.other_organization.id)
        args = {'year':1405,'half':2,'ma_min':1002,'ma_max':1002,'organization_id':self.organization.id,
                'province':'خراسان رضوی','subject_description':'عمرانی','letter_number':'ABC',
                'ad_text':'پل','amount_min':4500,'approved_amount_max':4500,'ad_type':'دولتی، سفارشی',
                'print_status':'چاپ شده','columns':['ma_reference','ad_text']}
        response = self.app.test_client().get('/reports', query_string=args)
        self.assertEqual(response.status_code, 200)
        self.assertIn('ساخت پل', response.get_data(as_text=True))
        export = self.client.get('/reports', query_string={**args,'export':'xlsx'})
        book = load_workbook(io.BytesIO(export.data))
        self.assertEqual(list(book.active.values), [('شماره م/الف','متن آگهی'),(1002,'ساخت پل')])

    def test_approval_default_excludes_null_but_allocation_date_includes(self):
        self.add_ad(ma_reference=1002)
        client = self.app.test_client()
        no_date = client.get('/reports?year=1405&half=2&export=xlsx')
        with_date = client.get('/reports?year=1405&half=2&export=xlsx&date_field=allocation_date')
        self.assertEqual(load_workbook(io.BytesIO(no_date.data)).active.max_row, 1)
        self.assertEqual(load_workbook(io.BytesIO(with_date.data)).active.max_row, 2)

    def test_bad_ranges_and_columns_are_rejected(self):
        for args in ({'start':'1404/01/01','end':'1405/01/01'}, {'start':'1405/02/01','end':'1405/01/01'},
                     {'ma_min':20,'ma_max':10}, {'columns':'password_hash'}, {'date_field':'password_hash'},
                     {'ad_type':'invalid'}, {'column_selection':'1'}):
            self.assertEqual(self.client.get('/reports',query_string=args).status_code,400)

    def test_group_targets_and_media_actuals_ignore_organization_filters(self):
        self.volumes([125,125,125,125,250,250])
        response = self.client.get('/reports',query_string={'year':1405,'half':2,'view':'media',
                                    'organization_id':self.other_organization.id,'export':'xlsx'})
        book = load_workbook(io.BytesIO(response.data))
        self.assertEqual(book.active.cell(2,3).value, '125')
        self.assertEqual(book['گروه‌ها'].cell(2,5).value, '250')
        self.assertEqual(book.active.max_column, 5)

    def test_exports_escape_spreadsheet_formulas_and_html_is_escaped(self):
        self.organization.name = '=HYPERLINK("bad")'
        db.session.commit()
        self.add_ad(ad_text='<script>alert(1)</script>')
        response = self.client.get('/reports?year=1405&half=2&date_field=allocation_date&export=xlsx&columns=organization')
        value = load_workbook(io.BytesIO(response.data)).active.cell(2,1).value
        self.assertTrue(value.startswith("'="))
        response = self.client.get('/reports?year=1405&half=2&date_field=allocation_date&columns=ad_text')
        self.assertNotIn('<script>alert(1)</script>', response.get_data(as_text=True))

    def test_created_at_filter_uses_tehran_calendar_day(self):
        self.add_ad(created_at=datetime(2026,9,30,21,0))
        response = self.client.get('/reports', query_string={'year':1405,'half':2,'date_field':'created_at',
                            'start':'2026-10-01','end':'2026-10-01','export':'xlsx'})
        self.assertEqual(load_workbook(io.BytesIO(response.data)).active.max_row, 2)

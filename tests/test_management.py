import json
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from sobhe import allocations as a
from sobhe.models import Advertisement, AuditEvent, Outlet, OutletTarget, User, db
from tests.support import AppTestCase


class SeedAdminTests(AppTestCase):
    def test_seed_creates_requested_account_and_password_is_hashed(self):
        result = self.app.test_cli_runner().invoke(args=['seed-admin'])
        self.assertEqual(result.exit_code, 0, result.output)
        account = User.query.filter_by(username='admin').one()
        self.assertEqual(account.role, 'admin')
        self.assertTrue(account.active)
        self.assertTrue(check_password_hash(account.password_hash, '123asd#'))
        self.assertNotEqual(account.password_hash, '123asd#')
        self.assertNotIn('123asd#', result.output)
        self.assertNotIn('123asd#', AuditEvent.query.filter_by(action='admin_seed').one().detail)

    def test_reseeding_restores_account_without_duplicates_or_changing_other_users(self):
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['seed-admin']).exit_code, 0)
        account = User.query.filter_by(username='admin').one()
        account_id, version = account.id, account.auth_version
        other_hash = self.operator.password_hash
        account.role, account.active = 'operator', False
        account.password_hash = generate_password_hash('temporary-password')
        db.session.commit()
        self.assertEqual(runner.invoke(args=['seed-admin']).exit_code, 0)
        db.session.expire_all()
        account = User.query.filter_by(username='admin').one()
        self.assertEqual(account.id, account_id)
        self.assertEqual(account.auth_version, version + 1)
        self.assertTrue(account.active)
        self.assertEqual(account.role, 'admin')
        self.assertTrue(check_password_hash(account.password_hash, '123asd#'))
        self.assertEqual(self.operator.password_hash, other_hash)

    def test_seeded_credentials_can_login_and_old_session_is_invalidated(self):
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['seed-admin']).exit_code, 0)
        client = self.app.test_client()
        self.assertEqual(client.post('/login',data={'username':'admin','password':'123asd#'}).status_code,302)
        self.assertEqual(client.get('/admin/users').status_code,200)
        self.assertEqual(runner.invoke(args=['seed-admin']).exit_code,0)
        self.assertEqual(client.get('/admin/users').status_code,302)


class MediaManagementTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in(admin=True)

    def test_create_read_update_and_delete_unused_media(self):
        result = self.client.post('/admin/catalog/outlets',data={'name':'رسانه جدید','media_type':'national'})
        self.assertEqual(result.status_code,302)
        media = Outlet.query.filter_by(name='رسانه جدید').one()
        media_id = media.id
        self.assertEqual(self.app.test_client().get(f'/media/{media.id}').status_code,200)
        self.assertEqual(self.client.get(f'/admin/media/{media.id}/edit').status_code,200)
        self.assertEqual(self.client.post('/admin/catalog/outlets',data={'id':media.id,'name':'رسانه ویرایش‌شده','media_type':'national'}).status_code,302)
        self.assertEqual(media.name,'رسانه ویرایش‌شده')
        self.assertEqual(self.client.post(f'/admin/media/{media.id}/delete').status_code,302)
        self.assertIsNone(db.session.get(Outlet,media_id))
        event = AuditEvent.query.filter_by(action='media_delete').one()
        self.assertEqual(json.loads(event.detail)['before']['name'],'رسانه ویرایش‌شده')

    def test_media_with_allocations_is_archived_without_losing_history_or_targets(self):
        ad = self.add_ad()
        media = self.outlets[0]
        target_id, revision = self.period.targets[0].id, self.period.revision
        self.assertEqual(self.client.post(f'/admin/media/{media.id}/delete').status_code,302)
        self.assertFalse(media.active)
        self.assertIsNotNone(db.session.get(Advertisement,ad.id))
        self.assertIsNotNone(db.session.get(OutletTarget,target_id))
        self.assertEqual(a.snapshot(self.period)['total'],'10')
        self.assertEqual(self.period.revision,revision + 1)
        self.assertEqual(AuditEvent.query.filter_by(action='media_archive').count(),1)

    def test_target_plan_alone_is_enough_to_preserve_media(self):
        media = self.outlets[0]
        self.client.post(f'/admin/media/{media.id}/delete')
        self.assertIsNotNone(db.session.get(Outlet,media.id))
        self.assertEqual(len(self.period.targets),6)
        self.assertFalse(media.active)

    def test_media_mutations_require_admin_and_csrf(self):
        media = self.outlets[0]
        self.sign_in()
        for path in ('/admin/media/new',f'/admin/media/{media.id}/edit'):
            self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.client.post(f'/admin/media/{media.id}/delete').status_code,403)
        self.assertTrue(media.active)
        self.sign_in(admin=True)
        self.app.config['CSRF_ENABLED'] = True
        self.assertEqual(self.client.post(f'/admin/media/{media.id}/delete').status_code,400)

    def test_public_media_profile_uses_selected_period_and_excludes_voided_records(self):
        previous = self.make_period(1404)
        self.add_ad(0,20,period=previous)
        self.add_ad(0,10)
        self.add_ad(0,80,voided_at=datetime.now())
        public = self.app.test_client()
        result = public.get(f'/media/{self.outlets[0].id}?year=1405')
        self.assertEqual(result.status_code,200)
        self.assertIn('1 آگهی',result.get_data(as_text=True))
        self.assertIn('>10 <small>کادر',result.get_data(as_text=True))


class AdvertisementManagementTests(AppTestCase):
    def test_staff_listing_defaults_to_allocation_date_so_new_ads_are_visible(self):
        ad = self.add_ad()
        result = self.client.get('/admin/advertisements?year=1405')
        self.assertEqual(result.status_code,200)
        self.assertIn(f'/admin/advertisements/{ad.id}',result.get_data(as_text=True))
        self.assertIn('ثبت آگهی جدید',result.get_data(as_text=True))

    def test_edit_and_delete_actions_are_admin_only(self):
        ad = self.add_ad()
        path='/reports?year=1405&date_field=allocation_date'
        self.assertNotIn('data-delete-ad',self.client.get(path).get_data(as_text=True))
        self.assertNotIn('data-delete-ad',self.app.test_client().get(path).get_data(as_text=True))
        self.sign_in(admin=True)
        self.assertNotIn('data-delete-ad',self.client.get(path).get_data(as_text=True))
        html=self.client.get('/admin/advertisements?year=1405').get_data(as_text=True)
        self.assertIn(f'/admin/advertisements/{ad.id}/edit',html)
        self.assertIn(f'data-delete-url="/admin/advertisements/{ad.id}/void"',html)

    def test_delete_from_listing_returns_to_list_and_recalculates_quota(self):
        ad=self.add_ad()
        self.sign_in(admin=True)
        result=self.client.post(f'/advertisements/{ad.id}/void',data={'version':ad.version,'reason':'ثبت تکراری','return_to':'reports'})
        self.assertEqual(result.status_code,302)
        self.assertIn('/admin/advertisements?',result.location)
        self.assertIsNotNone(ad.voided_at)
        self.assertEqual(a.snapshot(self.period)['total'],'0')
        self.assertEqual(self.app.test_client().get(f'/advertisements/{ad.id}').status_code,404)

    def test_stale_delete_does_not_remove_updated_advertisement(self):
        ad=self.add_ad()
        ad.version=2
        db.session.commit()
        self.sign_in(admin=True)
        self.client.post(f'/advertisements/{ad.id}/void',data={'version':1,'reason':'تلاش قدیمی','return_to':'reports'})
        self.assertIsNone(ad.voided_at)
        self.assertEqual(a.snapshot(self.period)['total'],'10')

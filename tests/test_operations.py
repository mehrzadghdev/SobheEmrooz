import io
import sqlite3
import zipfile
from contextlib import closing
from sqlalchemy import inspect

from sobhe import create_app
from sobhe import allocations as a
from sobhe.commands import backup, migrate, seed_demo
from sobhe.models import *
from sobhe.services import boxes, parse_date, period_key, period_bounds, units
from tests.support import AppTestCase, png_bytes


class OperationTests(AppTestCase):
    def test_cli_initialization_is_idempotent_and_never_resets_accounts(self):
        before = self.admin.password_hash
        self.assertIn('already current', migrate())
        self.assertEqual(User.query.count(), 2)
        self.assertEqual(self.admin.password_hash, before)

    def test_bootstrap_requires_unique_personal_account_and_valid_password(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['create-admin','--username','new-admin','--name','مدیر جدید'],
                               input='personal-password\npersonal-password\n')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIsNotNone(User.query.filter_by(username='new-admin').first())
        result = runner.invoke(args=['create-admin','--username','new-admin','--name','مدیر دیگر'],
                               input='another-password\nanother-password\n')
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(User.query.filter_by(username='new-admin').count(), 1)

    def test_password_recovery_invalidates_sessions(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['reset-password','--username','operator'], input='replacement-password\nreplacement-password\n')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.client.get('/advertisements/new').status_code, 302)

    def test_manual_media_target_submissions_are_rejected(self):
        self.sign_in(admin=True)
        data = {'revision':self.period.revision,'active':'1'}
        for target in self.period.targets:
            data.update({f'include_{target.outlet_id}':'1', f'category_{target.outlet_id}':target.category,
                         f'share_{target.outlet_id}':'10'})
        revision = self.period.revision
        self.client.post(f'/admin/periods/{self.period.id}',data=data)
        self.assertEqual(self.period.revision, revision)
        self.assertTrue(all(target.share_bp == 0 for target in self.period.targets))

    def test_period_activation_changes_only_selected_period(self):
        previous = self.make_period(1404)
        self.sign_in(admin=True)
        revision = self.period.revision
        self.client.post(f'/admin/periods/{self.period.id}', data={'revision':revision})
        self.assertFalse(self.period.active)
        self.assertTrue(previous.active)
        self.assertEqual(self.period.revision, revision + 1)
        self.assertEqual(len(self.period.targets),6)

    def test_period_update_never_removes_media_with_history(self):
        self.add_ad()
        self.sign_in(admin=True)
        revision = self.period.revision
        self.client.post(f'/admin/periods/{self.period.id}',data={'revision':revision,'active':'1'})
        self.assertEqual(len(self.period.targets),6)
        self.assertEqual(a.snapshot(self.period)['total'], '10')

    def test_new_period_enrolls_current_media_as_draft(self):
        self.sign_in(admin=True)
        self.outlets[0].category = 'national'
        self.outlets[1].active = False
        db.session.commit()
        response = self.client.post('/admin/periods',data={'year':'1406',})
        self.assertEqual(response.status_code,302)
        period = Period.query.filter_by(year=1406).one()
        self.assertFalse(period.active)
        self.assertEqual(len(period.targets),5)
        self.assertTrue(all(target.share_bp == 0 for target in period.targets))
        self.assertEqual(period.targets[0].category, 'national')

    def test_stale_target_revision_prevents_advertisement_save(self):
        data = self.data()
        data['preview_token'] = self.preview(data)['token']
        self.period.revision += 1
        db.session.commit()
        self.assertEqual(self.client.post('/advertisements/new',data=data).status_code,409)
        self.assertEqual(Advertisement.query.count(),0)

    def test_stale_record_version_prevents_overwrite(self):
        ad = self.add_ad()
        self.sign_in(admin=True)
        from sobhe.records import form_values
        data = form_values(ad)
        data.update(override_confirmed='1',preview_token=self.preview(data,ad)['token'],version='1')
        ad.version = 2
        db.session.commit()
        self.assertEqual(self.client.post(f'/advertisements/{ad.id}/edit',data=data).status_code,400)
        self.assertEqual(ad.version,2)

    def test_backup_contains_readable_database_and_corresponding_files(self):
        data = self.data()
        data['preview_token'] = self.preview(data)['token']
        data['file'] = (io.BytesIO(png_bytes()),'آگهی.png')
        self.assertEqual(self.client.post('/advertisements/new',data=data).status_code,302)
        ad = Advertisement.query.one()
        archive_path = backup(self.root / 'backup.zip')
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(archive.read('storage/' + ad.file_key),png_bytes())
            archive.extract('sobhe.db',self.root / 'restored')
        with closing(sqlite3.connect(self.root / 'restored/sobhe.db')) as restored:
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(restored.execute('SELECT volume_units FROM advertisement').fetchone()[0],1000)
        self.assertEqual(archive_path.stat().st_mode & 0o777,0o600)

    def test_demo_seed_refuses_to_modify_an_existing_instance(self):
        with self.assertRaises(Exception):
            seed_demo()
        self.assertEqual(Advertisement.query.count(),0)

    def test_new_app_does_not_create_schema_on_startup(self):
        instance = self.root / 'fresh'
        instance.mkdir()
        app = create_app({'TESTING':True,'SECRET_KEY':'fresh-test'},instance_path=str(instance))
        with app.app_context():
            self.assertEqual(inspect(db.engine).get_table_names(),[])
            db.session.remove()
            db.engine.dispose()

    def test_demo_seed_succeeds_in_fresh_instance_and_public_reports_render(self):
        instance = self.root / 'demo'
        instance.mkdir()
        app = create_app({'TESTING':True,'SECRET_KEY':'demo-test'},instance_path=str(instance))
        with app.app_context():
            migrate()
            db.session.add(User(username='personal-admin',name='مدیر نمونه',role='admin',active=True,
                                password_hash=self.password_hash))
            db.session.commit()
            seed_demo()
            self.assertEqual(Advertisement.query.count(),8)
            state = a.snapshot(Period.query.one())
            self.assertEqual(state['total'],'1000')
            self.assertEqual(state['local_percent'],'60')
            client = app.test_client()
            for path in ('/','/reports','/reports?view=media','/reports?view=organizations','/advertisements/1'):
                self.assertEqual(client.get(path).status_code,200,path)
            self.assertIn('نسخه نمایشی',client.get('/').get_data(as_text=True))
            db.session.remove()
            db.engine.dispose()


class PersianDateTests(AppTestCase):
    def test_shahrivar_mehr_and_new_year_boundaries(self):
        for value,expected in [('1405/01/01',1405),('1405/06/31',1405),('1405/07/01',1405),
                               ('1405/12/29',1405),('1406/01/01',1406)]:
            self.assertEqual(period_key(parse_date(value)),expected)

    def test_esfand_leap_day_and_invalid_day(self):
        self.assertEqual(period_key(parse_date('۱۴۰۳/۱۲/۳۰')),1403)
        with self.assertRaises(ValueError):
            parse_date('1404/12/30')

    def test_limits_are_exact_and_money_and_volume_differ(self):
        self.assertEqual(units('۱٫۲۵'),125)
        self.assertEqual(boxes(125),'1.25')
        self.assertEqual(units('٠.١٠'),10)
        with self.assertRaises(ValueError):
            units('1.001')

"""Coverage for automatic group policy, historical membership, and upgrades."""
import io
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import event
from sqlalchemy.schema import CreateTable, CreateIndex

from sobhe import create_app, allocations as a
from sobhe.commands import migrate
from sobhe.constants import CATEGORIES
from sobhe.models import Advertisement, AuditEvent, Outlet, OutletTarget, Period, SchemaVersion, db
from sobhe.services import units
from tests.support import AppTestCase


class AutomaticMediaTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in(admin=True)

    def save_media(self, media=None, **changes):
        values = {'name': media.name if media else 'رسانه تازه', 'active': '1',
                  'media_type': 'local'}
        if media:
            values['id'] = media.id
        values.update(changes)
        return self.client.post('/admin/catalog/outlets', data=values)

    def test_local_and_national_types_are_required_and_persisted(self):
        for index, category in enumerate(CATEGORIES):
            result = self.save_media(name=f'رسانه {index}', media_type=category)
            self.assertEqual(result.status_code, 302)
            media = Outlet.query.filter_by(name=f'رسانه {index}').one()
            self.assertEqual(media.category, category)
            membership = OutletTarget.query.filter_by(period_id=self.period.id, outlet_id=media.id).one()
            self.assertEqual(membership.category, category)

    def test_invalid_types_rollback_and_keep_entered_values(self):
        for changes in ({'media_type':''}, {'media_type':'unknown'}):
            result = self.save_media(**changes)
            self.assertEqual(result.status_code, 400)
            self.assertIn('رسانه تازه', result.get_data(as_text=True))
        self.assertEqual(Outlet.query.count(), 6)
        self.assertEqual(OutletTarget.query.count(), 6)
        self.assertEqual(AuditEvent.query.count(), 0)

    def test_failed_edit_preserves_type_name_and_membership(self):
        result = self.save_media(self.outlets[0], name='نام نامعتبر', media_type='')
        self.assertEqual(result.status_code,400)
        self.assertEqual(self.outlets[0].name,'صبح امروز')
        self.assertEqual(self.outlets[0].category,'local')
        self.assertEqual(self.period.targets[0].category,'local')

    def test_new_media_is_immediately_available_and_invalidates_preview(self):
        data = self.data()
        data['preview_token'] = self.preview(data)['token']
        self.assertEqual(self.save_media().status_code,302)
        media = Outlet.query.filter_by(name='رسانه تازه').one()
        self.assertIn(media.id,[row['id'] for row in self.preview()['options']])
        self.assertEqual(self.client.post('/admin/advertisements/new',data=data).status_code,409)
        self.assertEqual(Advertisement.query.count(),0)

    def test_reactivation_enrolls_existing_periods_without_duplicates(self):
        self.save_media(active='')
        media = Outlet.query.filter_by(name='رسانه تازه').one()
        self.assertFalse(OutletTarget.query.filter_by(outlet_id=media.id).count())
        self.save_media(media)
        self.save_media(media)
        self.assertEqual(OutletTarget.query.filter_by(outlet_id=media.id).count(),1)
        self.assertIn(media.id, [row['id'] for row in self.preview()['options']])

    def test_type_changes_preserve_used_and_voided_period_categories(self):
        previous = self.make_period(1404)
        unused = self.make_period(1406)
        self.add_ad(0,10)
        self.add_ad(0,20,period=previous,voided_at=datetime(2026,1,1))
        media = self.outlets[0]
        self.assertEqual(self.save_media(media,media_type='national').status_code,302)
        self.assertEqual(media.category,'national')
        self.assertEqual(self.period.targets[0].category,'local')
        self.assertEqual(previous.targets[0].category,'local')
        self.assertEqual(unused.targets[0].category,'national')
        self.assertEqual(a.snapshot(self.period)['groups']['local']['allocated'],'10')
        event_row = AuditEvent.query.filter_by(action='catalog_update').one()
        self.assertEqual(set(json.loads(event_row.detail)['membership']['preserved_categories']),{self.period.id,previous.id})
        self.client.post('/admin/periods',data={'year':'1407'})
        future = Period.query.filter_by(year=1407).one()
        self.assertEqual(future.targets[0].category,'national')

    def test_missing_group_warns_without_redistributing_fixed_rates(self):
        for media in self.outlets[:4]:
            self.save_media(media,active='')
        result = self.preview(self.data(outlet_id=''))
        self.assertEqual([group['target_percent'] for group in result['groups'].values()],[50,50])
        self.assertTrue(any('تقسیم نمی‌شود' in warning for warning in result['warnings']))
        self.assertIsNone(result['recommended'])

    def test_period_form_rejects_manual_media_configuration_and_keeps_history(self):
        self.add_ad()
        for changes in ({'share_1':'99'}, {'category_1':'national'}, {'include_1':'0'}):
            revision = self.period.revision
            result = self.client.post(f'/admin/periods/{self.period.id}',
                data={'revision':revision,'active':'1',**changes},follow_redirects=True)
            self.assertIn('سهم هدف فقط برای گروه‌ها', result.get_data(as_text=True))
            self.assertEqual(self.period.revision,revision)
            self.assertEqual(len(self.period.targets),6)
            self.assertEqual(a.snapshot(self.period)['groups']['local']['allocated'],'10')

    def test_group_only_reports_exports_and_preview(self):
        self.volumes([130,170,100,100,250,250])
        for path in ('/?year=1405','/reports?view=media&year=1405',
                     f'/media/{self.outlets[0].id}?year=1405'):
            result = self.client.get(path)
            self.assertEqual(result.status_code,200)
            self.assertNotIn('سهم هدف در گروه',result.get_data(as_text=True))
        preview = self.preview()
        for row in (preview['recommended'],preview['selected']):
            for removed in ('share_bp','target_share','target_percent','expected','projected_expected','exceeded'):
                self.assertNotIn(removed,row)
        group = preview['groups']['local']
        self.assertEqual((group['allocated'],group['expected'],group['projected_allocated'],group['projected_expected']),
                         ('500','500','510','505'))
        response = self.client.get('/reports?view=media&year=1405&export=xlsx')
        workbook = load_workbook(io.BytesIO(response.data))
        self.assertEqual(workbook.active.max_column,5)
        self.assertEqual(workbook['گروه‌ها'].max_row,3)
        self.assertEqual([workbook['گروه‌ها'].cell(row,2).value for row in range(2,4)],[50,50])

    def test_thousand_media_period_uses_constant_form_and_bounded_reads(self):
        db.session.add_all([Outlet(name=f'رسانه بزرگ {index}',active=True,category=CATEGORIES[index % 2]) for index in range(994)])
        db.session.commit()
        self.client.post('/admin/periods',data={'year':'1406',})
        period = Period.query.filter_by(year=1406).one()
        self.assertEqual(len(period.targets),1000)
        result = self.client.get(f'/admin/periods/{period.id}')
        html = result.get_data(as_text=True)
        self.assertEqual(result.status_code,200)
        self.assertNotIn('name="share_',html)
        self.assertLess(html.count('<input'),5)
        period_id = period.id
        db.session.remove()
        statements = []
        def record(connection,cursor,statement,parameters,context,executemany):
            statements.append(statement)
        event.listen(db.engine,'before_cursor_execute',record)
        try:
            state = a.snapshot(db.session.get(Period,period_id))
        finally:
            event.remove(db.engine,'before_cursor_execute',record)
        self.assertEqual(len(state['rows']),1000)
        self.assertLessEqual(len(statements),4)
        self.assertEqual(sum(group['active_count'] for group in state['groups'].values()),1000)

    def test_tie_breaking_uses_oldest_last_allocation_then_id(self):
        self.volumes([10,10,30,30,100,100])
        result = a.preview(self.period,units(5))
        self.assertEqual(result['recommended']['outlet_id'],self.outlets[0].id)
        self.assertEqual(a.preview(self.period,units(5))['recommended'],result['recommended'])


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sobhe-upgrade-')
        self.root = Path(self.temp.name)
        self.app = create_app({'TESTING':True,'SECRET_KEY':'upgrade-test',
                               'SQLALCHEMY_DATABASE_URI':'sqlite:///' + str(self.root / 'legacy.db')},
                              instance_path=str(self.root))
        self.context = self.app.app_context()
        self.context.push()
        with db.engine.begin() as connection:
            for table in db.metadata.sorted_tables:
                if table.name == 'outlet':
                    connection.exec_driver_sql('CREATE TABLE outlet (id INTEGER PRIMARY KEY, name VARCHAR(150) UNIQUE NOT NULL, active BOOLEAN NOT NULL)')
                elif table.name == 'period':
                    connection.exec_driver_sql('CREATE TABLE period (id INTEGER PRIMARY KEY, year INTEGER NOT NULL, half INTEGER NOT NULL, active BOOLEAN NOT NULL, recovery BOOLEAN NOT NULL, revision INTEGER NOT NULL, UNIQUE(year,half), CHECK(half IN (1,2)))')
                else:
                    connection.exec_driver_sql(str(CreateTable(table).compile(db.engine)).replace("category IN ('local','national')", "category IN ('local_one','local_two','national')"))
                for index in table.indexes:
                    connection.execute(CreateIndex(index))
            connection.exec_driver_sql("INSERT INTO schema_version VALUES (1, '2026-01-01')")
            connection.exec_driver_sql("INSERT INTO outlet VALUES (1,'A',1),(2,'B',1),(3,'Pending',1),(4,'Inactive',0)")
            connection.exec_driver_sql('INSERT INTO period (id,year,half,active,recovery,revision) VALUES (1,1404,1,1,0,1),(2,1405,2,1,0,1)')
            connection.exec_driver_sql("INSERT INTO outlet_target (period_id,outlet_id,category,share_bp) VALUES (1,1,'local_one',4000),(2,1,'national',6000),(1,2,'local_two',10000),(1,4,'national',0)")
            connection.exec_driver_sql("INSERT INTO user (id,username,name,password_hash,role,active,auth_version) VALUES (1,'admin','Admin','hash','admin',1,1)")
            connection.exec_driver_sql("INSERT INTO organization (id,name,active) VALUES (1,'Org',1)")
        db.session.add(Advertisement(ma_reference=1002,organization_id=1,outlet_id=1,period_id=1,
                                     allocation_date=date(2025,4,1),volume_units=1234,creator_id=1))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.temp.cleanup()

    def test_upgrade_preserves_history_infers_latest_type_and_enrolls_missing_media(self):
        self.assertIn('version 5',migrate())
        self.assertEqual(db.session.get(Outlet,1).category,'national')
        self.assertEqual(db.session.get(Outlet,2).category,'local')
        self.assertIsNone(db.session.get(Outlet,3).category)
        old = OutletTarget.query.filter_by(period_id=1,outlet_id=1).one()
        self.assertEqual((old.category,old.share_bp),('local',4000))
        added = OutletTarget.query.filter_by(period_id=2,outlet_id=2).one()
        self.assertEqual((added.category,added.share_bp),('local',0))
        self.assertEqual(OutletTarget.query.filter_by(outlet_id=3).count(),0)
        self.assertEqual(OutletTarget.query.filter_by(outlet_id=4).count(),1)
        self.assertEqual(a.snapshot(db.session.get(Period,1))['total'],'12.34')
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['user_id'], session['auth_version'] = 1, 1
        self.assertIn('نیازمند تعیین نوع', client.get('/admin/catalog/outlets').get_data(as_text=True))
        self.assertIn('هنوز نوع مشخصی ندارند', client.get('/admin/periods/1').get_data(as_text=True))
        rejected = client.get('/api/allocation-preview', query_string={
            'allocation_date':'2026-04-01','volume':'1','outlet_id':3})
        self.assertEqual(rejected.status_code,400)
        count = OutletTarget.query.count()
        self.assertIn('already current',migrate())
        self.assertEqual(OutletTarget.query.count(),count)
        self.assertEqual([row.version for row in SchemaVersion.query.order_by(SchemaVersion.version)],[1,2,3,4,5,6])
        with db.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('PRAGMA integrity_check').scalar_one(),'ok')
            self.assertFalse(connection.exec_driver_sql('PRAGMA foreign_key_check').all())

    def test_broken_history_aborts_without_partial_schema_changes(self):
        db.session.query(OutletTarget).filter_by(period_id=1,outlet_id=1).delete()
        db.session.commit()
        with self.assertRaisesRegex(Exception,'Historical advertisements'):
            migrate()
        with db.engine.connect() as connection:
            self.assertNotIn('category',[row[1] for row in connection.exec_driver_sql('PRAGMA table_info(outlet)')])
        self.assertEqual(SchemaVersion.query.count(),1)

    def test_failure_after_alter_rolls_back_and_can_be_retried(self):
        def fail_after_alter(connection,cursor,statement,parameters,context,executemany):
            if statement.startswith('UPDATE outlet SET category'):
                raise RuntimeError('simulated interruption')
        event.listen(db.engine,'before_cursor_execute',fail_after_alter)
        try:
            with self.assertRaisesRegex(RuntimeError,'simulated interruption'):
                migrate()
        finally:
            event.remove(db.engine,'before_cursor_execute',fail_after_alter)
        with db.engine.connect() as connection:
            self.assertNotIn('category',[row[1] for row in connection.exec_driver_sql('PRAGMA table_info(outlet)')])
        self.assertEqual(SchemaVersion.query.count(),1)
        self.assertIn('version 5',migrate())

    def prepare_version_two(self):
        db.session.rollback()
        with db.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE outlet ADD COLUMN category VARCHAR(20) CHECK (category IN ('local_one','local_two','national'))")
            connection.exec_driver_sql("UPDATE outlet SET category = CASE id WHEN 1 THEN 'national' WHEN 2 THEN 'local_two' WHEN 4 THEN 'national' END")
            connection.exec_driver_sql("INSERT INTO outlet_target (period_id,outlet_id,category,share_bp) VALUES (2,2,'local_two',0)")
            connection.exec_driver_sql("INSERT INTO schema_version VALUES (2,'2026-01-02')")
            connection.exec_driver_sql("INSERT INTO setting VALUES ('label_local_one','Old first'),('label_local_two','Old second')")
        db.session.expire_all()

    def test_version_two_merge_preserves_records_and_combines_local_totals(self):
        self.prepare_version_two()
        db.session.add_all([
            Advertisement(ma_reference=1003,organization_id=1,outlet_id=2,period_id=1,
                          allocation_date=date(2025,4,1),volume_units=2000,creator_id=1),
            Advertisement(ma_reference=1004,organization_id=1,outlet_id=4,period_id=1,
                          allocation_date=date(2025,4,1),volume_units=3000,creator_id=1),
            Advertisement(ma_reference=1005,organization_id=1,outlet_id=2,period_id=1,
                          allocation_date=date(2025,4,1),volume_units=1000,creator_id=1,
                          voided_at=datetime(2026,4,2))])
        db.session.commit()
        with db.engine.connect() as connection:
            records = connection.exec_driver_sql('SELECT * FROM advertisement ORDER BY id').all()
            targets = connection.exec_driver_sql('SELECT id,period_id,outlet_id,share_bp FROM outlet_target ORDER BY id').all()
        self.assertIn('version 5', migrate())
        with db.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('SELECT * FROM advertisement ORDER BY id').all(), records)
            self.assertEqual(connection.exec_driver_sql('SELECT id,period_id,outlet_id,share_bp FROM outlet_target ORDER BY id').all(), targets)
            self.assertFalse(connection.exec_driver_sql("SELECT * FROM setting WHERE key LIKE 'label_local_%'").all())
            self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one(), 1)
            self.assertFalse(connection.exec_driver_sql('PRAGMA foreign_key_check').all())
            self.assertEqual(connection.exec_driver_sql('PRAGMA integrity_check').scalar_one(), 'ok')
        state = a.snapshot(db.session.get(Period,1))
        self.assertEqual(state['categories'], {'local':3234,'national':3000})
        self.assertEqual([group['expected'] for group in state['groups'].values()], ['31.17','31.17'])
        self.assertEqual(db.session.get(Outlet,1).category, 'national')
        self.assertEqual([target.category for target in state['targets']], ['local','local','national'])
        self.assertEqual(Advertisement.query.count(),4)
        self.assertIn('already current',migrate())

    def test_version_two_failure_after_table_rebuild_is_atomic_and_retryable(self):
        self.prepare_version_two()
        def fail_after_rebuild(connection,cursor,statement,parameters,context,executemany):
            if statement.startswith('INSERT INTO period_yearly'):
                raise RuntimeError('simulated merge interruption')
        event.listen(db.engine,'before_cursor_execute',fail_after_rebuild)
        try:
            with self.assertRaisesRegex(RuntimeError,'simulated merge interruption'):
                migrate()
        finally:
            event.remove(db.engine,'before_cursor_execute',fail_after_rebuild)
        self.assertEqual([row.version for row in SchemaVersion.query.order_by(SchemaVersion.version)], [1,2])
        self.assertEqual(db.session.get(Outlet,2).category, 'local_two')
        self.assertEqual(OutletTarget.query.filter_by(period_id=1,outlet_id=1).one().category, 'local_one')
        with db.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one(),1)
            self.assertFalse(connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name LIKE '%_merged'").all())
        self.assertIn('version 5',migrate())

    def prepare_version_three_same_year(self):
        from sobhe.commands import _upgrade_media_categories
        self.prepare_version_two()
        db.session.rollback()
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
            try:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
                _upgrade_media_categories(connection, [1,2])
                connection.exec_driver_sql('UPDATE period SET year = 1405 WHERE id = 1')
                connection.exec_driver_sql("UPDATE advertisement SET allocation_date = '2026-04-01'")
                connection.commit()
            finally:
                connection.rollback()
                connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        db.session.expire_all()

    def test_yearly_upgrade_combines_halves_and_preserves_ads_and_categories(self):
        self.prepare_version_three_same_year()
        db.session.add_all([
            Advertisement(ma_reference=1003,organization_id=1,outlet_id=2,period_id=2,
                          allocation_date=date(2026,10,1),volume_units=2000,creator_id=1),
            Advertisement(ma_reference=1004,organization_id=1,outlet_id=4,period_id=1,
                          allocation_date=date(2026,4,1),volume_units=3000,creator_id=1),
            Advertisement(ma_reference=1005,organization_id=1,outlet_id=2,period_id=2,
                          allocation_date=date(2026,10,1),volume_units=500,creator_id=1,
                          voided_at=datetime(2026,10,2))])
        db.session.commit()
        with db.engine.begin() as connection:
            connection.exec_driver_sql('UPDATE period SET active = 0 WHERE id = 1')
            before = [dict(row) for row in connection.exec_driver_sql('SELECT * FROM advertisement ORDER BY id').mappings()]
        self.assertIn('version 5',migrate())
        period = Period.query.one()
        self.assertEqual((period.id,period.year,period.active,period.revision),(1,1405,True,2))
        self.assertFalse(period.recovery)
        self.assertEqual(len(period.targets),3)
        self.assertEqual([target.category for target in period.targets],['local','local','national'])
        self.assertEqual(a.snapshot(period)['categories'],{'local':3234,'national':3000})
        self.assertEqual(a.find_period(date(2026,4,1)).id,a.find_period(date(2026,10,1)).id)
        with db.engine.connect() as connection:
            after = [dict(row) for row in connection.exec_driver_sql('SELECT * FROM advertisement ORDER BY id').mappings()]
            for record in before:
                record['period_id'] = 1
            self.assertEqual(after,before)
            self.assertNotIn('half',[row[1] for row in connection.exec_driver_sql('PRAGMA table_info(period)')])
            self.assertFalse(connection.exec_driver_sql('PRAGMA foreign_key_check').all())
        audit = AuditEvent.query.filter_by(action='periods_yearly_migration').one()
        detail = json.loads(audit.detail)
        self.assertEqual(detail['period_mapping'],{'1':1,'2':1})
        self.assertEqual(len(detail['previous_memberships']),5)
        self.assertIn('already current',migrate())

    def test_yearly_upgrade_preserves_recovery_until_combined_share_reaches_50(self):
        self.prepare_version_three_same_year()
        db.session.get(Outlet,4).active = True
        db.session.add(Advertisement(ma_reference=1003,organization_id=1,outlet_id=4,period_id=1,
                                     allocation_date=date(2026,4,1),volume_units=1000,creator_id=1))
        db.session.commit()
        with db.engine.begin() as connection:
            connection.exec_driver_sql('UPDATE period SET recovery = 1 WHERE id = 2')
        migrate()
        period = Period.query.one()
        self.assertTrue(period.recovery)
        self.assertEqual(a.preview(period,1)['recommended']['category'],'national')

    def test_yearly_category_conflicts_abort_without_reclassifying_history(self):
        self.prepare_version_three_same_year()
        db.session.add(Advertisement(ma_reference=1003,organization_id=1,outlet_id=1,period_id=2,
                                     allocation_date=date(2026,10,1),volume_units=1000,creator_id=1,
                                     voided_at=datetime(2026,10,2)))
        db.session.commit()
        with self.assertRaisesRegex(Exception,'historical classification conflict'):
            migrate()
        with db.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('SELECT COUNT(*) FROM period').scalar_one(),2)
            self.assertEqual(connection.exec_driver_sql('SELECT MAX(version) FROM schema_version').scalar_one(),3)
            self.assertEqual(connection.exec_driver_sql('SELECT period_id FROM advertisement WHERE ma_reference = 1003').scalar_one(),2)
            self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one(),1)
            self.assertFalse(connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name LIKE '%_yearly'").all())

    def test_yearly_failure_after_reassignment_rolls_back_and_can_retry(self):
        self.prepare_version_three_same_year()
        def interrupt(connection,cursor,statement,parameters,context,executemany):
            if statement == 'DROP TABLE period':
                raise RuntimeError('yearly merge interrupted')
        event.listen(db.engine,'before_cursor_execute',interrupt)
        try:
            with self.assertRaisesRegex(RuntimeError,'yearly merge interrupted'):
                migrate()
        finally:
            event.remove(db.engine,'before_cursor_execute',interrupt)
        with db.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('SELECT COUNT(*) FROM period').scalar_one(),2)
            self.assertEqual(connection.exec_driver_sql('SELECT COUNT(*) FROM outlet_target').scalar_one(),5)
            self.assertEqual(connection.exec_driver_sql('SELECT MAX(version) FROM schema_version').scalar_one(),3)
            self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one(),1)
        self.assertIn('version 5',migrate())

"""Minimal ad entry, optional allocation data, and the admin list."""
import io
from html.parser import HTMLParser
from openpyxl import load_workbook

from sobhe import allocations as a, records as r
from sobhe.models import Advertisement, AuditEvent, db
from sobhe.services import today
from tests.support import AppTestCase


class AdFormMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_ad_form = False
        self.required = []
        self.open_details = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form' and attrs.get('id') == 'advertisement-form':
            self.in_ad_form = True
        if self.in_ad_form:
            if tag in ('input','select','textarea') and 'required' in attrs:
                self.required.append(attrs.get('name'))
            if tag == 'details' and 'open' in attrs:
                self.open_details.append(attrs)
    def handle_endtag(self,tag):
        if tag == 'form': self.in_ad_form = False


class OptionalAdFieldTests(AppTestCase):
    def minimal(self, **changes):
        values = {'ma_reference':'9101','organization_id':str(self.organization.id),
                  'outlet_id':str(self.outlets[0].id),'volume':'2.5','override_confirmed':'1'}
        values.update(changes)
        return values

    def save_minimal(self, data):
        response = self.client.get('/api/allocation-preview',query_string={
            'volume':data['volume'],'outlet_id':data['outlet_id'],
            'allocation_date':data.get('allocation_date','')})
        self.assertEqual(response.status_code,200)
        data['preview_token'] = response.json['token']
        return self.client.post('/admin/advertisements/new',data=data)

    def test_minimum_required_fields_save_with_optional_date_omitted(self):
        self.assertEqual(self.save_minimal(self.minimal()).status_code,302)
        ad = Advertisement.query.one()
        self.assertEqual(ad.volume_units,250)
        self.assertEqual(ad.allocation_date,today())
        self.assertEqual(a.snapshot(ad.period)['total'],'2.5')
        for path in (f'/admin/advertisements/{ad.id}',f'/advertisements/{ad.id}',
                     f'/media/{ad.outlet_id}', '/reports?view=media','/'):
            self.assertEqual(self.client.get(path).status_code,200,path)

    def test_all_four_primary_fields_are_required(self):
        for missing in ('ma_reference','organization_id','outlet_id','volume'):
            data = self.minimal()
            data.pop(missing)
            self.assertEqual(self.client.post('/admin/advertisements/new',data=data).status_code,400)
        self.assertEqual(Advertisement.query.count(),0)

    def test_blank_and_invalid_volumes_are_rejected_but_date_is_optional(self):
        for value in ('','0','-1','0.001','invalid'):
            self.assertEqual(self.client.post('/admin/advertisements/new',data=self.minimal(volume=value)).status_code,400)
        self.assertEqual(self.save_minimal(self.minimal(allocation_date='')).status_code,302)
        self.assertEqual(Advertisement.query.one().volume_units,250)

    def test_volume_can_be_added_to_legacy_record_with_a_reviewed_preview(self):
        ad = self.add_ad()
        ad.volume_units = None
        db.session.commit()
        self.sign_in(admin=True)
        data = r.form_values(ad)
        self.assertEqual(data['volume'],'')
        data.update(volume='2.5',override_confirmed='1')
        self.assertEqual(self.submit(data,ad).status_code,302)
        self.assertEqual(ad.volume_units,250)
        self.assertEqual(a.snapshot(ad.period)['total'],'2.5')

    def test_clearing_required_volume_does_not_change_saved_record(self):
        previous = self.make_period(1404)
        ad = self.add_ad(0,10,period=previous)
        saved_date = ad.allocation_date
        self.sign_in(admin=True)
        data = self.minimal(ma_reference=str(ad.ma_reference),version=str(ad.version),volume='',allocation_date='')
        self.assertEqual(self.client.post(f'/admin/advertisements/{ad.id}/edit',data=data).status_code,400)
        self.assertEqual(ad.volume_units,1000)
        self.assertEqual(ad.allocation_date,saved_date)
        self.assertEqual(ad.period_id,previous.id)
        self.assertEqual(a.snapshot(previous)['total'],'10')

    def test_date_can_be_omitted_for_a_volume_preview_and_save(self):
        self.assertEqual(self.save_minimal(self.minimal(volume='10')).status_code,302)
        self.assertEqual(Advertisement.query.one().volume_units,1000)

    def test_four_form_controls_are_required_and_accordions_start_closed(self):
        self.sign_in(admin=True)
        ad = self.add_ad()
        for path in ('/admin/advertisements/new',f'/admin/advertisements/{ad.id}/edit'):
            markup = AdFormMarkup()
            markup.feed(self.client.get(path).get_data(as_text=True))
            self.assertEqual(set(markup.required),{'ma_reference','organization_id','outlet_id','volume'})
            self.assertFalse(markup.open_details)

    def test_admin_listing_and_export_include_all_years_without_search_or_summaries(self):
        current = self.add_ad()
        previous = self.make_period(1404)
        old = self.add_ad(1,20,period=previous)
        self.add_ad(voided_at=today())
        response = self.client.get('/admin/advertisements')
        html = response.get_data(as_text=True)
        for removed in ('report-filter-form','period-controls','filtered-stats','report-result-heading'):
            self.assertNotIn(removed,html)
        self.assertIn(f'/admin/advertisements/{current.id}',html)
        self.assertIn(f'/admin/advertisements/{old.id}',html)
        response = self.client.get('/admin/advertisements?export=xlsx&columns=ma_reference')
        book = load_workbook(io.BytesIO(response.data))
        self.assertEqual({row[0] for row in book.active.iter_rows(min_row=2,values_only=True)},
                         {current.ma_reference,old.ma_reference})

    def test_media_drilldown_links_still_limit_the_admin_list_to_that_media(self):
        first = self.add_ad(0,10)
        other = self.add_ad(1,20)
        response = self.client.get(f'/admin/advertisements?outlet_id={first.outlet_id}')
        html = response.get_data(as_text=True)
        self.assertIn(f'/admin/advertisements/{first.id}',html)
        self.assertNotIn(f'/admin/advertisements/{other.id}',html)





class OptionalVolumeMigrationTests(AppTestCase):
    def prepare_version_four(self):
        from sqlalchemy.schema import CreateIndex, CreateTable
        self.add_ad(0,'2.5',notes='Preserve original metadata')
        db.session.rollback()
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
            try:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
                ddl = str(CreateTable(Advertisement.__table__).compile(db.engine))
                ddl = ddl.replace('CREATE TABLE advertisement (','CREATE TABLE advertisement_legacy (')
                ddl = ddl.replace('volume_units BIGINT,','volume_units BIGINT NOT NULL,')
                connection.exec_driver_sql(ddl)
                connection.exec_driver_sql('INSERT INTO advertisement_legacy SELECT * FROM advertisement')
                connection.exec_driver_sql('DROP TABLE advertisement')
                connection.exec_driver_sql('ALTER TABLE advertisement_legacy RENAME TO advertisement')
                for index in Advertisement.__table__.indexes:
                    connection.execute(CreateIndex(index))
                connection.exec_driver_sql('DELETE FROM schema_version WHERE version >= 5')
                connection.commit()
            finally:
                connection.rollback()
                connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        db.session.expire_all()

    def test_upgrade_preserves_records_and_allows_unspecified_volume(self):
        from sobhe.commands import migrate
        self.prepare_version_four()
        with db.engine.connect() as connection:
            before = connection.exec_driver_sql('SELECT * FROM advertisement').all()
            self.assertTrue(next(row[3] for row in connection.exec_driver_sql('PRAGMA table_info(advertisement)') if row[1] == 'volume_units'))
        self.assertIn('version 5',migrate())
        with db.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('SELECT * FROM advertisement').all(),before)
            self.assertFalse(next(row[3] for row in connection.exec_driver_sql('PRAGMA table_info(advertisement)') if row[1] == 'volume_units'))
            self.assertFalse(connection.exec_driver_sql('PRAGMA foreign_key_check').all())
            self.assertEqual(connection.exec_driver_sql('PRAGMA integrity_check').scalar_one(),'ok')
        data = {'ma_reference':'9102','organization_id':self.organization.id,'outlet_id':self.outlets[0].id}
        self.assertEqual(self.client.post('/admin/advertisements/new',data=data).status_code,400)
        legacy = Advertisement.query.one()
        legacy.volume_units = None
        db.session.commit()
        self.assertIsNone(legacy.volume_units)
        self.assertIn('already current',migrate())

    def test_interrupted_table_rebuild_rolls_back_and_can_retry(self):
        from sqlalchemy import event
        from sobhe.commands import migrate
        self.prepare_version_four()
        def interrupt(connection,cursor,statement,parameters,context,executemany):
            if statement == 'DROP TABLE advertisement':
                raise RuntimeError('optional volume migration interrupted')
        event.listen(db.engine,'before_cursor_execute',interrupt)
        try:
            with self.assertRaisesRegex(RuntimeError,'optional volume migration interrupted'):
                migrate()
        finally:
            event.remove(db.engine,'before_cursor_execute',interrupt)
        with db.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('SELECT MAX(version) FROM schema_version').scalar_one(),4)
            self.assertEqual(connection.exec_driver_sql('SELECT volume_units FROM advertisement').scalar_one(),250)
            self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one(),1)
            self.assertFalse(connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name = 'advertisement_optional'").all())
        self.assertIn('version 5',migrate())

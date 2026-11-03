"""Full Persian-year allocation boundaries, reports, and period management."""
import io
from datetime import date

from openpyxl import load_workbook

from sobhe import allocations as a
from sobhe.models import Period
from sobhe.services import parse_date, period_bounds
from tests.support import AppTestCase


class YearlyPeriodTests(AppTestCase):
    def test_all_dates_in_year_resolve_to_same_period(self):
        for value in ('1405/01/01','1405/06/31','1405/07/01','1405/12/29'):
            self.assertEqual(a.find_period(parse_date(value)).id, self.period.id)
        self.assertIsNone(a.find_period(parse_date('1406/01/01')))
        following = self.make_period(1406)
        self.assertEqual(a.find_period(parse_date('1406/01/01')).id, following.id)

    def test_full_year_bounds_include_leap_day(self):
        start, end = period_bounds(1403)
        self.assertEqual(start, date(2024,3,20))
        self.assertEqual(end, date(2025,3,21))
        self.assertEqual((end-start).days, 366)
        self.assertLess(parse_date('1403/12/30'), end)

    def test_both_former_halves_contribute_to_annual_reports_and_quotas(self):
        first = self.add_ad(0,10,allocation_date=parse_date('1405/01/01'))
        last = self.add_ad(4,30,allocation_date=parse_date('1405/12/29'))
        previous = self.make_period(1404)
        self.add_ad(0,90,period=previous)
        state = a.snapshot(self.period)
        self.assertEqual(state['total'], '40')
        self.assertEqual([group['expected'] for group in state['groups'].values()], ['20','20'])
        response = self.client.get('/reports?year=1405&date_field=allocation_date&export=xlsx&columns=ma_reference')
        book = load_workbook(io.BytesIO(response.data))
        self.assertEqual({row[0] for row in book.active.iter_rows(min_row=2, values_only=True)},
                         {first.ma_reference,last.ma_reference})

    def test_period_form_only_accepts_one_period_per_year(self):
        self.sign_in(admin=True)
        response = self.client.get('/admin/periods')
        self.assertNotIn('name="half"', response.get_data(as_text=True))
        response = self.client.post('/admin/periods',data={'year':'1406'})
        self.assertEqual(response.status_code,302)
        response = self.client.post('/admin/periods',data={'year':'1406','half':'2'})
        self.assertIn('قبلاً ایجاد شده',response.get_data(as_text=True))
        self.assertEqual(Period.query.filter_by(year=1406).count(),1)
        self.assertEqual(len(Period.query.filter_by(year=1406).one().targets),6)

    def test_preview_and_public_links_use_year_without_half(self):
        preview = self.preview(self.data(allocation_date='1405/01/01'))
        self.assertEqual(preview['period_year'],1405)
        self.assertNotIn('period_half',preview)
        html = self.client.get('/?year=1405&half=2').get_data(as_text=True)
        self.assertNotIn('name="half"',html)
        self.assertNotIn('half=',html)
        self.assertIn('فروردین تا اسفند',html)

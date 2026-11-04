"""Shared bar/pie chart scopes, historical media classification, and group targets."""
import json
import re
from datetime import datetime

from sobhe.models import db
from sobhe.services import parse_date
from tests.support import AppTestCase


class PublicChartTests(AppTestCase):
    def chart_data(self, path, kind='pie'):
        response = self.app.test_client().get(path)
        self.assertEqual(response.status_code,200)
        match = re.search(rf'<script type="application/json" data-report-chart-data data-chart-kind="{kind}">(.*?)</script>',
                          response.get_data(as_text=True),re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_each_chart_defaults_to_yearly_and_uses_its_own_period(self):
        self.add_ad(0,12,allocation_date=parse_date('1405/01/01'))
        self.add_ad(4,88,allocation_date=parse_date('1405/02/01'))
        for kind in ('bar', 'pie'):
            data = self.chart_data('/reports?year=1405', kind)
            self.assertEqual((data['window']['mode'], data['total']), ('yearly','100'))
        path = '/reports?year=1405&chart_bar_range=monthly&chart_bar_month=1'
        self.assertEqual(self.chart_data(path, 'bar')['total'], '12')
        self.assertEqual(self.chart_data(path, 'pie')['window']['mode'], 'yearly')
        self.assertEqual(self.chart_data(path, 'pie')['total'], '100')
        path += '&chart_pie_range=monthly&chart_pie_month=2'
        self.assertEqual(self.chart_data(path, 'bar')['total'], '12')
        self.assertEqual(self.chart_data(path, 'pie')['total'], '88')
        path = '/?year=1405&chart_pie_range=weekly&chart_pie_date=1405/01/01'
        self.assertEqual(self.chart_data(path, 'bar')['window']['mode'], 'yearly')
        self.assertEqual(self.chart_data(path, 'bar')['total'], '100')
        self.assertEqual(self.chart_data(path, 'pie')['total'], '12')

    def test_percentages_and_drilldown_use_volume_and_group_denominators(self):
        self.add_ad(0,30)
        self.add_ad(1,10)
        self.add_ad(4,60)
        data = self.chart_data('/reports?year=1405&view=media')
        local,national = data['groups']
        self.assertEqual((local['percent'],national['percent']),('40','60'))
        self.assertEqual([(row['allocated'],row['percent']) for row in local['media'][:2]],
                         [('30','75'),('10','25')])
        self.assertEqual(national['media'][0]['percent'],'100')
        self.assertEqual(local['media'][0]['url'],f'/media/{self.outlets[0].id}?year=1405')
        self.assertEqual(data['total'],'100')

    def test_yearly_and_monthly_totals_exclude_voided_and_other_years(self):
        self.add_ad(0,'0.25',allocation_date=parse_date('1405/01/01'))
        self.add_ad(4,12,allocation_date=parse_date('1405/07/01'))
        self.add_ad(0,10,allocation_date=parse_date('1405/12/29'))
        self.add_ad(0,100,voided_at=datetime(2026,10,1))
        previous = self.make_period(1404)
        self.add_ad(0,90,period=previous)
        data = self.chart_data('/reports?year=1405')
        self.assertEqual(data['window']['mode'], 'yearly')
        self.assertEqual(data['total_units'],2225)
        self.assertEqual(data['total'],'22.25')
        self.assertEqual(len(data['buckets']),12)
        self.assertEqual(data['buckets'][0]['local'],25)
        self.assertEqual(data['buckets'][6]['national'],1200)
        self.assertEqual(data['buckets'][11]['local'],1000)
        for month, local, national in ((1,25,0), (7,0,1200), (12,1000,0), (2,0,0)):
            data = self.chart_data(f'/reports?year=1405&chart_pie_range=monthly&chart_pie_month={month}')
            self.assertEqual(data['total_units'], local + national)
            self.assertEqual([group['units'] for group in data['groups']], [local, national])
            self.assertEqual(data['window']['month'], month)
            self.assertEqual(sum(bucket['local'] for bucket in data['buckets']),local)
            self.assertEqual(sum(bucket['national'] for bucket in data['buckets']),national)
            self.assertEqual(len(data['buckets']),31 if month in (1,2) else 30 if month == 7 else 29)

    def test_all_public_reports_share_full_year_scope_despite_table_filters(self):
        self.add_ad(0,10)
        self.add_ad(4,30)
        expected = self.chart_data('/?year=1405')
        for view in ('ads','media','organizations'):
            self.assertEqual(self.chart_data(f'/reports?year=1405&view={view}&ma_min=999999'),expected)
        response = self.client.get('/admin/advertisements?year=1405')
        self.assertNotIn('data-report-charts',response.get_data(as_text=True))

    def test_chart_classification_follows_recorded_year_category(self):
        self.add_ad(0,10)
        self.outlets[0].category = 'national'
        db.session.commit()
        data = self.chart_data('/reports?year=1405')
        self.assertEqual(data['groups'][0]['units'],1000)
        self.assertEqual(data['groups'][1]['units'],0)
        data = self.chart_data('/reports?year=1405&chart_pie_range=monthly&chart_pie_month=1')
        self.assertEqual(data['groups'][0]['units'],1000)

    def test_empty_year_has_zero_data_without_fabricated_shares(self):
        data = self.chart_data('/reports?year=1406')
        self.assertEqual(data['total_units'],0)
        self.assertEqual([group['percent'] for group in data['groups']],['0','0'])
        self.assertEqual([group['status_class'] for group in data['groups']],['muted','muted'])

    def test_week_starts_saturday_and_ends_friday_with_matching_drilldown(self):
        self.add_ad(0,10,allocation_date=parse_date('2026-10-02'))
        self.add_ad(0,12,allocation_date=parse_date('2026-10-03'))
        self.add_ad(1,8,allocation_date=parse_date('2026-10-09'))
        self.add_ad(4,80,allocation_date=parse_date('2026-10-07'))
        self.add_ad(0,100,allocation_date=parse_date('2026-10-10'))
        data = self.chart_data('/reports?year=1405&chart_pie_range=weekly&chart_pie_date=2026-10-07')
        self.assertEqual((data['window']['start'], data['window']['end']), ('2026-10-03','2026-10-09'))
        local, national = data['groups']
        self.assertEqual((data['total'], local['percent'], national['percent']), ('100','20','80'))
        self.assertEqual([(row['allocated'],row['percent']) for row in local['media'][:2]], [('12','60'),('8','40')])
        self.assertEqual(len(data['buckets']),7)
        self.assertEqual([bucket['local'] for bucket in data['buckets']], [1200,0,0,0,0,0,800])
        self.assertEqual(data['buckets'][4]['national'],8000)
        self.assertEqual(data['buckets'][0]['label'],'شنبه')

    def test_week_is_clipped_at_persian_year_boundary(self):
        self.add_ad(0,10,allocation_date=parse_date('1403/12/30'),period=self.make_period(1403))
        self.add_ad(0,20,allocation_date=parse_date('1404/01/01'),period=self.make_period(1404))
        data = self.chart_data('/reports?year=1404&chart_pie_range=weekly&chart_pie_date=1404/01/01')
        self.assertEqual(data['window']['start'], parse_date('1404/01/01').isoformat())
        self.assertEqual(data['window']['end'], parse_date('1404/01/01').isoformat())
        self.assertEqual(data['total'],'20')
        self.assertEqual(len(data['buckets']),1)

    def test_chart_range_is_shared_by_all_public_views_without_filtering_the_tables(self):
        self.add_ad(0,12,allocation_date=parse_date('1405/01/01'))
        self.add_ad(4,88,allocation_date=parse_date('1405/02/01'))
        expected = self.chart_data('/?year=1405&chart_pie_range=monthly&chart_pie_month=1')
        for view in ('ads','media','organizations'):
            self.assertEqual(self.chart_data(f'/reports?year=1405&view={view}&chart_pie_range=monthly&chart_pie_month=1&ma_min=999999'),expected)
        self.assertEqual(expected['total'],'12')

    def test_below_target_shares_are_warnings_and_only_balanced_shares_are_green(self):
        self.add_ad(0,12)
        self.add_ad(4,88)
        data = self.chart_data('/reports?year=1405')
        self.assertEqual([group['status_class'] for group in data['groups']],['warning','warning'])
        self.assertEqual(data['groups'][0]['status_label'], 'کمتر از هدف گروه')
        response = self.client.get(f'/media/{self.outlets[0].id}?year=1405')
        self.assertIn('data-allocation-status="below-target"', response.text)
        self.assertIn('media-target-card warning', response.text)
        self.add_ad(0,76)
        data = self.chart_data('/reports?year=1405')
        self.assertEqual([group['status_class'] for group in data['groups']],['success','success'])

    def test_invalid_chart_ranges_are_rejected(self):
        for args in ('chart_pie_range=invalid', 'chart_pie_range=monthly&chart_pie_month=13',
                     'chart_bar_range=invalid', 'chart_bar_range=monthly&chart_bar_month=13',
                     'chart_pie_range=weekly&chart_pie_date=invalid', 'chart_pie_range=weekly&chart_pie_date=1404/01/01'):
            for path in ('/', '/reports'):
                self.assertEqual(self.client.get(f'{path}?year=1405&{args}').status_code,400)

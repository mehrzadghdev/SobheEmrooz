"""Annual chart totals, historical media classification, and public scope."""
import json
import re
from datetime import datetime

from sobhe.models import db
from sobhe.services import parse_date
from tests.support import AppTestCase


class PublicChartTests(AppTestCase):
    def chart_data(self, path):
        response = self.app.test_client().get(path)
        self.assertEqual(response.status_code,200)
        match = re.search(r'<script type="application/json" data-report-chart-data>(.*?)</script>',
                          response.get_data(as_text=True),re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

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

    def test_monthly_values_cover_both_halves_and_exclude_voided_and_other_years(self):
        self.add_ad(0,'0.25',allocation_date=parse_date('1405/01/01'))
        self.add_ad(4,12,allocation_date=parse_date('1405/07/01'))
        self.add_ad(0,10,allocation_date=parse_date('1405/12/29'))
        self.add_ad(0,100,voided_at=datetime(2026,10,1))
        previous = self.make_period(1404)
        self.add_ad(0,90,period=previous)
        data = self.chart_data('/reports?year=1405')
        self.assertEqual(len(data['months']),12)
        self.assertEqual(data['months'][0],{'name':'فروردین','local':25,'national':0})
        self.assertEqual(data['months'][6]['national'],1200)
        self.assertEqual(data['months'][11]['local'],1000)
        self.assertEqual(sum(month['local']+month['national'] for month in data['months']),2225)
        self.assertEqual(data['total'],'22.25')

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
        self.assertEqual(data['months'][0]['local'],1000)

    def test_empty_year_has_zero_data_without_fabricated_shares(self):
        data = self.chart_data('/reports?year=1406')
        self.assertEqual(data['total_units'],0)
        self.assertEqual([group['percent'] for group in data['groups']],['0','0'])
        self.assertTrue(all(not month['local'] and not month['national'] for month in data['months']))

from fractions import Fraction

from sobhe import allocations as a
from sobhe.models import db
from sobhe.services import units
from tests.support import AppTestCase


class AllocationTests(AppTestCase):
    def test_1000_box_targets_are_25_25_50(self):
        self.volumes([125, 125, 125, 125, 250, 250])
        state = a.snapshot(self.period)
        self.assertEqual(state['total'], '1000')
        self.assertEqual(state['categories'], {'local_one':25000, 'local_two':25000, 'national':50000})
        self.assertEqual([row['expected'] for row in state['rows']], ['125','125','125','125','250','250'])
        self.assertFalse(any(row['exceeded'] for row in state['rows']))

    def test_quota_uses_overall_target_not_category_actual(self):
        self.volumes([130,170,100,100,250,250])
        row = a.snapshot(self.period)['rows'][0]
        self.assertEqual(row['expected'], '125')
        self.assertEqual(row['category_percent'], '43.33')
        self.assertTrue(row['exceeded'])
        self.assertEqual(row['excess'], '5')

    def test_projected_65_percent_recommends_national(self):
        self.volumes([300,0,250,0,450,0])
        preview = a.preview(self.period, units(300))
        self.assertEqual(preview['recommended']['category'], 'national')
        self.assertTrue(preview['recovery'])
        self.assertFalse(self.period.recovery, 'A read-only preview must not persist recovery mode')

    def test_large_ad_crossing_from_below_50_also_recommends_national(self):
        self.volumes([200,0,200,0,600,0])
        preview = a.preview(self.period, units(1000))
        self.assertEqual(preview['recommended']['category'], 'national')

    def test_exactly_60_projected_is_still_local(self):
        self.volumes([300,0,250,0,450,0])
        preview = a.preview(self.period, units(125))
        self.assertNotEqual(preview['recommended']['category'], 'national')
        self.assertEqual(preview['projected_local_percent'], '60')

    def test_recovery_continues_below_60_until_50(self):
        self.volumes([300,0,250,0,450,0])
        self.period.recovery = True
        db.session.commit()
        self.assertEqual(a.preview(self.period, units(1))['recommended']['category'], 'national')
        self.add_ad(4, 100)
        a.update_recovery(self.period)
        db.session.commit()
        self.assertFalse(self.period.recovery)
        self.assertNotEqual(a.preview(self.period, units(1))['recommended']['category'], 'national')

    def test_manual_excess_enters_recovery(self):
        self.volumes([300,0,250,0,450,0])
        response = self.submit(self.data(volume='200'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.period.recovery)
        self.assertEqual(a.snapshot(self.period)['local_percent'], '62.5')

    def test_local_category_with_larger_shortfall_has_priority(self):
        self.volumes([200,0,100,0,300,0])
        self.assertEqual(a.preview(self.period, units(1))['recommended']['category'], 'local_two')

    def test_relative_shortfall_favors_under_target_small_outlet(self):
        self.period.targets[0].share_bp, self.period.targets[1].share_bp = 9000, 1000
        db.session.commit()
        self.volumes([50,0,100,0,500,0])
        result = a.preview(self.period, units(1))
        self.assertEqual(result['recommended']['outlet_id'], self.outlets[1].id)

    def test_startup_is_local_and_deterministic_with_clear_warning(self):
        first, second = a.preview(self.period, units(10)), a.preview(self.period, units(10))
        self.assertEqual(first['recommended']['outlet_id'], second['recommended']['outlet_id'])
        self.assertNotEqual(first['recommended']['category'], 'national')
        self.assertEqual(first['local_percent'], '0')
        self.assertTrue(first['warnings'])

    def test_zero_share_and_inactive_outlets_are_never_recommended(self):
        self.period.targets[0].share_bp, self.period.targets[1].share_bp = 0, 10000
        self.outlets[1].active = False
        db.session.commit()
        result = a.preview(self.period, units(1))
        self.assertEqual(result['recommended']['category'], 'local_two')
        manual = a.preview(self.period, units(1), self.outlets[0].id)
        self.assertTrue(manual['selected']['projected_exceeded'])

    def test_missing_preferred_group_shows_manual_choices(self):
        for outlet in self.outlets[:4]:
            outlet.active = False
        db.session.commit()
        result = a.preview(self.period, units(1))
        self.assertIsNone(result['recommended'])
        self.assertEqual(len(result['options']), 2)
        self.assertTrue(result['warnings'])

    def test_fractional_boxes_remain_exact(self):
        self.add_ad(0, '0.1')
        self.add_ad(1, '0.2')
        state = a.snapshot(self.period)
        self.assertEqual(state['total_units'], 30)
        self.assertEqual(state['total'], '0.3')
        self.assertEqual(state['rows'][0]['expected_units'], Fraction(15,4))

    def test_displayed_quota_does_not_hide_small_excess_by_rounding(self):
        self.volumes(['0.01',0,0,0,'0.03',0])
        row = a.snapshot(self.period)['rows'][0]
        self.assertEqual(row['expected'],'0.005')
        self.assertEqual(row['allocated'],'0.01')
        self.assertTrue(row['exceeded'])

    def test_periods_keep_separate_targets_and_counts(self):
        other = self.make_period(1405, 1)
        other.targets[0].share_bp, other.targets[1].share_bp = 2500, 7500
        db.session.commit()
        self.add_ad(0, 100, period=other)
        self.add_ad(0, 10)
        self.assertEqual(a.snapshot(other)['rows'][0]['target_share'], '25')
        self.assertEqual(a.snapshot(self.period)['rows'][0]['target_share'], '50')
        self.assertEqual(a.snapshot(self.period)['total'], '10')

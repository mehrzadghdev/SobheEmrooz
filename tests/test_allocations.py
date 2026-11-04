from fractions import Fraction

from sobhe import allocations as a
from sobhe.models import Advertisement, db
from sobhe.services import units
from tests.support import AppTestCase


class AllocationTests(AppTestCase):
    def test_1000_box_targets_are_50_50(self):
        self.volumes([125, 125, 125, 125, 250, 250])
        state = a.snapshot(self.period)
        self.assertEqual(state['total'], '1000')
        self.assertEqual(state['categories'], {'local':50000, 'national':50000})
        self.assertEqual([group['expected'] for group in state['groups'].values()], ['500','500'])
        self.assertFalse(any(group['exceeded'] for group in state['groups'].values()))
        self.assertTrue(all('expected' not in row and 'target_share' not in row for row in state['rows']))

    def test_quota_uses_overall_target_not_category_actual(self):
        self.volumes([180,170,100,100,250,200])
        state = a.snapshot(self.period)
        row = state['rows'][0]
        group = state['groups']['local']
        self.assertEqual(group['expected'], '500')
        self.assertEqual(row['category_percent'], '32.73')
        self.assertTrue(group['exceeded'])
        self.assertEqual(group['excess'], '50')

    def test_large_ad_recommends_local_above_60_percent(self):
        self.volumes([300,0,250,0,450,0])
        preview = a.preview(self.period, units(300))
        self.assertEqual(preview['recommended']['category'], 'local')
        self.assertTrue(preview['recovery'])
        self.assertFalse(self.period.recovery, 'A read-only preview must not persist recovery mode')

    def test_large_ad_crossing_from_below_50_still_recommends_local(self):
        self.volumes([200,0,200,0,600,0])
        preview = a.preview(self.period, units(1000))
        self.assertEqual(preview['recommended']['category'], 'local')

    def test_exactly_60_projected_is_still_local(self):
        self.volumes([300,0,250,0,450,0])
        preview = a.preview(self.period, units(125))
        self.assertNotEqual(preview['recommended']['category'], 'national')
        self.assertEqual(preview['projected_local_percent'], '60')

    def test_size_rule_overrides_recovery_before_and_after_50_percent(self):
        self.volumes([300,0,250,0,450,0])
        self.period.recovery = True
        db.session.commit()
        self.assertEqual(a.preview(self.period, units(1))['recommended']['category'], 'national')
        self.assertEqual(a.preview(self.period, units(5))['recommended']['category'], 'local')
        self.add_ad(4, 100)
        a.update_recovery(self.period)
        db.session.commit()
        self.assertFalse(self.period.recovery)
        self.assertEqual(a.preview(self.period, units(1))['recommended']['category'], 'national')
        self.assertEqual(a.preview(self.period, units(5))['recommended']['category'], 'local')

    def test_manual_excess_enters_recovery(self):
        self.volumes([300,0,250,0,450,0])
        response = self.submit(self.data(volume='200'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.period.recovery)
        self.assertEqual(a.snapshot(self.period)['local_percent'], '62.5')

    def test_lowest_volume_is_chosen_across_all_local_media(self):
        self.volumes([200,0,100,0,300,0])
        self.assertEqual(a.preview(self.period, units(5))['recommended']['outlet_id'], self.outlets[1].id)

    def test_lowest_volume_ignores_legacy_individual_shares(self):
        self.period.targets[0].share_bp, self.period.targets[1].share_bp = 9000, 1000
        db.session.commit()
        self.volumes([90,20,200,30,500,0])
        result = a.preview(self.period, units(5))
        self.assertEqual(result['recommended']['outlet_id'], self.outlets[1].id)

    def test_startup_is_local_and_deterministic_with_clear_warning(self):
        first, second = a.preview(self.period, units(10)), a.preview(self.period, units(10))
        self.assertEqual(first['recommended']['outlet_id'], second['recommended']['outlet_id'])
        self.assertNotEqual(first['recommended']['category'], 'national')
        self.assertEqual(first['local_percent'], '0')
        self.assertTrue(first['warnings'])

    def test_legacy_zero_share_is_eligible_but_inactive_media_are_not(self):
        self.period.targets[0].share_bp, self.period.targets[1].share_bp = 0, 10000
        self.outlets[1].active = False
        db.session.commit()
        result = a.preview(self.period, units(5))
        self.assertEqual(result['recommended']['outlet_id'], self.outlets[0].id)
        self.assertNotIn(self.outlets[1].id, [row['id'] for row in result['options']])
        self.assertTrue(result['groups']['local']['projected_exceeded'])

    def test_missing_preferred_group_shows_manual_choices(self):
        for outlet in self.outlets[:4]:
            outlet.active = False
        db.session.commit()
        result = a.preview(self.period, units(5))
        self.assertIsNone(result['recommended'])
        self.assertEqual(len(result['options']), 2)
        self.assertTrue(result['warnings'])

    def test_size_boundary_ignores_local_share_and_recovery(self):
        for values in ([0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 100, 0],
                       [100, 0, 0, 0, 100, 0], [200, 0, 0, 0, 100, 0],
                       [100, 0, 0, 0, 0, 0]):
            Advertisement.query.delete()
            db.session.commit()
            self.volumes(values)
            for recovery in (False, True):
                self.period.recovery = recovery
                db.session.commit()
                for volume, category in (('0.01', 'national'), ('4.99', 'national'),
                                         ('5', 'local'), ('5.01', 'local'), ('1000', 'local')):
                    with self.subTest(values=values, recovery=recovery, volume=volume):
                        result = a.preview(self.period, units(volume))
                        self.assertEqual(result['recommended']['category'], category)
                        self.assertEqual(self.period.recovery, recovery, 'Preview must remain read-only')

    def test_national_group_uses_lowest_volume_for_small_ads(self):
        self.volumes([10, 10, 10, 10, 30, 20])
        result = a.preview(self.period, units('4.99'))
        self.assertEqual(result['recommended']['outlet_id'], self.outlets[5].id)

    def test_missing_national_group_never_falls_back_to_local(self):
        for outlet in self.outlets[4:]:
            outlet.active = False
        db.session.commit()
        result = a.preview(self.period, units('4.99'))
        self.assertIsNone(result['recommended'])
        self.assertTrue(result['warnings'])

    def test_fractional_boxes_remain_exact(self):
        self.add_ad(0, '0.1')
        self.add_ad(1, '0.2')
        state = a.snapshot(self.period)
        self.assertEqual(state['total_units'], 30)
        self.assertEqual(state['total'], '0.3')
        self.assertEqual(state['groups']['local']['expected_units'], Fraction(15))

    def test_displayed_quota_does_not_hide_small_excess_by_rounding(self):
        self.volumes(['0.03',0,0,0,'0.02',0])
        row = a.snapshot(self.period)['groups']['local']
        self.assertEqual(row['expected'],'0.025')
        self.assertEqual(row['allocated'],'0.03')
        self.assertTrue(row['exceeded'])

    def test_periods_keep_separate_group_totals(self):
        other = self.make_period(1404)
        other.targets[0].share_bp, other.targets[1].share_bp = 2500, 7500
        db.session.commit()
        self.add_ad(0, 100, period=other)
        self.add_ad(0, 10)
        self.assertEqual(a.snapshot(other)['groups']['local']['allocated'], '100')
        self.assertEqual(a.snapshot(self.period)['groups']['local']['allocated'], '10')
        self.assertEqual(a.snapshot(self.period)['total'], '10')

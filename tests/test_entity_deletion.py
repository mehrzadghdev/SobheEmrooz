import json

from sobhe import allocations as a
from sobhe.models import Advertisement, AuditEvent, Organization, Outlet, OutletTarget, Period, Subject, User, db, now
from tests.support import AppTestCase


class EntityDeletionTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in(admin=True)

    def subject(self):
        row = Subject(name='موضوع آزمایش حذف', active=True)
        db.session.add(row)
        db.session.commit()
        return row

    def user(self, **changes):
        values = dict(username='unused-user', name='حساب آزمایش حذف', role='operator', active=True,
                      password_hash=self.password_hash)
        values.update(changes)
        row = User(**values)
        db.session.add(row)
        db.session.commit()
        return row

    def test_unused_catalog_entries_can_be_deleted_with_an_audit_record(self):
        for kind, row in (('organizations', self.other_organization), ('subjects', self.subject())):
            with self.subTest(kind=kind):
                row_id, row_name, model = row.id, row.name, type(row)
                response = self.client.post(f'/admin/catalog/{kind}/{row_id}/delete')
                self.assertEqual(response.status_code, 302)
                self.assertIsNone(db.session.get(model, row_id))
                event = AuditEvent.query.filter_by(action='catalog_delete', target=f'{kind}:{row_id}').one()
                self.assertEqual(json.loads(event.detail)['before']['name'], row_name)

    def test_referenced_catalog_entries_are_archived_and_reports_remain_readable(self):
        subject = self.subject()
        ad = self.add_ad(subject_id=subject.id)
        for kind, row in (('organizations', self.organization), ('subjects', subject)):
            response = self.client.post(f'/admin/catalog/{kind}/{row.id}/delete')
            self.assertEqual(response.status_code, 302)
            self.assertFalse(row.active)
            self.assertIsNotNone(db.session.get(type(row), row.id))
        self.assertEqual(AuditEvent.query.filter_by(action='catalog_archive').count(), 2)
        self.assertEqual(a.snapshot(self.period)['total'], '10')
        self.assertEqual(self.app.test_client().get(f'/advertisements/{ad.id}').status_code, 200)
        self.assertEqual(ad.subject.name, subject.name)

    def test_voided_ads_still_preserve_catalog_and_period_references(self):
        subject = self.subject()
        self.add_ad(subject_id=subject.id, voided_at=now())
        for kind, row in (('organizations', self.organization), ('subjects', subject)):
            self.client.post(f'/admin/catalog/{kind}/{row.id}/delete')
            self.assertIsNotNone(db.session.get(type(row), row.id))
            self.assertFalse(row.active)
        self.client.post(f'/admin/periods/{self.period.id}/delete', data={'revision': self.period.revision})
        self.assertIsNotNone(db.session.get(Period, self.period.id))
        self.assertEqual(len(self.period.targets), 6)

    def test_deleting_an_empty_period_removes_only_its_targets(self):
        other = self.make_period(1405, 1)
        period_id, target_ids = self.period.id, [target.id for target in self.period.targets]
        response = self.client.post(f'/admin/periods/{period_id}/delete', data={'revision': self.period.revision})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(Period, period_id))
        self.assertTrue(all(db.session.get(OutletTarget, target_id) is None for target_id in target_ids))
        self.assertEqual(OutletTarget.query.filter_by(period_id=other.id).count(), 6)
        self.assertEqual(Outlet.query.count(), 6)
        event = AuditEvent.query.filter_by(action='period_delete').one()
        self.assertEqual(len(json.loads(event.detail)['before']['targets']), 6)

    def test_periods_with_ads_and_stale_period_deletions_are_rejected(self):
        ad = self.add_ad()
        response = self.client.post(f'/admin/periods/{self.period.id}/delete', data={'revision': self.period.revision}, follow_redirects=True)
        self.assertIn('دوره دارای سابقه آگهی قابل حذف نیست.', response.text)
        self.assertIsNotNone(db.session.get(Advertisement, ad.id))
        empty = self.make_period(1406, 1)
        revision = empty.revision
        empty.revision += 1
        db.session.commit()
        response = self.client.post(f'/admin/periods/{empty.id}/delete', data={'revision': revision}, follow_redirects=True)
        self.assertIn('برنامه دوره تغییر کرده است', response.text)
        self.assertIsNotNone(db.session.get(Period, empty.id))
        self.assertEqual(OutletTarget.query.filter_by(period_id=empty.id).count(), 6)
        self.assertEqual(AuditEvent.query.filter_by(action='period_delete').count(), 0)

    def test_unused_accounts_can_be_deleted_without_logging_passwords(self):
        user = self.user()
        user_id = user.id
        response = self.client.post(f'/admin/users/{user_id}/delete', data={'auth_version': user.auth_version})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(User, user_id))
        event = AuditEvent.query.filter_by(action='user_delete').one()
        self.assertEqual(json.loads(event.detail)['before']['username'], 'unused-user')
        self.assertNotIn(self.password_hash, event.detail)

    def test_archiving_an_ad_creator_preserves_ads_and_invalidates_existing_sessions(self):
        ad = self.add_ad()
        operator_client = self.app.test_client()
        self.sign_in(client=operator_client)
        version = self.operator.auth_version
        response = self.client.post(f'/admin/users/{self.operator.id}/delete', data={'auth_version': version})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.operator.active)
        self.assertEqual(self.operator.auth_version, version + 1)
        self.assertEqual(ad.creator.id, self.operator.id)
        self.assertEqual(operator_client.get('/admin/advertisements').status_code, 302)
        self.assertEqual(a.snapshot(self.period)['total'], '10')
        self.assertEqual(AuditEvent.query.filter_by(action='user_archive').count(), 1)

    def test_accounts_with_only_audit_history_are_also_preserved(self):
        db.session.add(AuditEvent(actor_id=self.operator.id, action='login', target=f'user:{self.operator.id}'))
        db.session.commit()
        event_id = AuditEvent.query.filter_by(actor_id=self.operator.id).one().id
        self.client.post(f'/admin/users/{self.operator.id}/delete', data={'auth_version': self.operator.auth_version})
        self.assertFalse(self.operator.active)
        self.assertEqual(db.session.get(AuditEvent, event_id).actor.name, self.operator.name)

    def test_current_account_and_last_active_admin_cannot_be_removed(self):
        path = f'/admin/users/{self.admin.id}/delete'
        self.client.post(path, data={'auth_version': self.admin.auth_version})
        self.assertTrue(self.admin.active)
        second = self.user(username='second-admin', role='admin')
        self.client.post(path, data={'auth_version': self.admin.auth_version})
        self.assertIsNotNone(db.session.get(User, self.admin.id))
        self.assertTrue(self.admin.active)
        self.assertTrue(second.active)
        self.assertEqual(AuditEvent.query.filter(AuditEvent.action.in_(['user_delete', 'user_archive'])).count(), 0)

    def test_stale_account_deletion_does_not_remove_changed_account(self):
        user = self.user()
        version = user.auth_version
        user.auth_version += 1
        db.session.commit()
        response = self.client.post(f'/admin/users/{user.id}/delete', data={'auth_version': version}, follow_redirects=True)
        self.assertIn('حساب کاربری تغییر کرده است', response.text)
        self.assertIsNotNone(db.session.get(User, user.id))
        self.assertTrue(user.active)

    def test_all_new_actions_require_admin_csrf_and_post(self):
        subject = self.subject()
        paths = (f'/admin/catalog/organizations/{self.organization.id}/delete',
                 f'/admin/catalog/subjects/{subject.id}/delete',
                 f'/admin/periods/{self.period.id}/delete', f'/admin/users/{self.operator.id}/delete')
        public = self.app.test_client()
        self.sign_in()
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(public.post(path).status_code, 302)
                self.assertEqual(self.client.post(path).status_code, 403)
                self.assertEqual(self.client.get(path).status_code, 405)
        self.sign_in(admin=True)
        self.app.config['CSRF_ENABLED'] = True
        for path in paths:
            self.assertEqual(self.client.post(path).status_code, 400)
        self.assertTrue(self.organization.active)
        self.assertTrue(subject.active)
        self.assertTrue(self.operator.active)

    def test_unknown_catalog_kinds_do_not_delete_records(self):
        self.assertEqual(self.client.post(f'/admin/catalog/users/{self.operator.id}/delete').status_code, 404)
        self.assertIsNotNone(db.session.get(User, self.operator.id))

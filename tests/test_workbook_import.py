from sobhe.models import Advertisement, Organization, Setting, db
from sobhe.workbook_import import import_rows, IMPORT_KEY
from tests.support import AppTestCase


class WorkbookImportTests(AppTestCase):
    def rows(self):
        return [dict(row=2, organization='سازمان واردشده', outlet='صبح امروز خراسان رضوی',
                     date='2026-10-25', volume=525, amount=125000000)]

    def test_dry_run_and_repeat(self):
        result = import_rows(self.rows(), self.admin.username, 'source.xlsx')
        self.assertEqual(result['boxes'], 5.25)
        self.assertEqual(Advertisement.query.count(), 0)
        self.assertIsNone(Organization.query.filter_by(name='سازمان واردشده').first())
        self.assertIsNone(db.session.get(Setting, IMPORT_KEY))
        # Clear read transaction before starting the serialized import.
        db.session.rollback()
        import_rows(self.rows(), 'administrator', 'source.xlsx', apply=True)
        self.assertEqual(Advertisement.query.one().volume_units, 525)
        db.session.rollback()
        self.assertTrue(import_rows(self.rows(), 'administrator', 'source.xlsx', apply=True)['already_imported'])
        self.assertEqual(Advertisement.query.count(), 1)

    def test_existing_year_history_refused(self):
        self.add_ad()
        db.session.rollback()
        with self.assertRaisesRegex(ValueError, 'already has advertisements'):
            import_rows(self.rows(), 'administrator', 'source.xlsx', apply=True)
        db.session.rollback()
        self.assertEqual(Advertisement.query.count(), 1)

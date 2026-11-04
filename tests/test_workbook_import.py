import json
import unicodedata
from datetime import datetime

from sobhe.models import Advertisement, AuditEvent, Organization, Setting, db
from unittest.mock import MagicMock, patch

from sobhe.workbook_import import import_rows, read_rows, resolve_workbook, HEADERS, IDENTIFIED_HEADERS, IMPORT_KEY
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

    def test_new_sheet_preserves_source_ids_and_fractional_volume(self):
        sheet = MagicMock(title='Sheet1')
        sheet.values = iter([IDENTIFIED_HEADERS])
        sheet.iter_rows.return_value = iter([
            (2318993, 'سازمان واردشده', 'صبح امروز خراسان رضوی', '۱۴۰۵/۰۸/۱۸', 5.25, 125000000),
            (2318993, 'سازمان واردشده', 'صبح امروز خراسان رضوی', '۱۴۰۵/۰۸/۲۰', 6, 150000000),
        ])
        workbook = MagicMock(sheetnames=['Sheet1'])
        workbook.__getitem__.return_value = sheet
        with patch('sobhe.workbook_import.load_workbook', return_value=workbook):
            rows = read_rows('12 آبان.xlsx')
        self.assertEqual([row['source_id'] for row in rows], [2318993, 2318993])
        self.assertEqual([row['volume'] for row in rows], [525, 600])
        self.assertEqual([row['row'] for row in rows], [2, 3])
        workbook.close.assert_called_once()

    def test_legacy_sheet_retains_original_row_format(self):
        sheet = MagicMock(title='Sheet2')
        sheet.values = iter([HEADERS])
        sheet.iter_rows.return_value = iter([
            ('سازمان واردشده', 'صبح امروز خراسان رضوی', '2026-10-25', 5.25, 125000000),
        ])
        workbook = MagicMock(sheetnames=['Sheet1', 'Sheet2'])
        workbook.__getitem__.return_value = sheet
        with patch('sobhe.workbook_import.load_workbook', return_value=workbook):
            self.assertEqual(read_rows('source.xlsx'), self.rows())

    def test_repeated_source_ids_keep_all_rows_with_unique_app_ids(self):
        rows = [dict(self.rows()[0], source_id=2318993),
                dict(self.rows()[0], row=3, source_id=2318993, date='2026-10-27')]
        result = import_rows(rows, 'administrator', '12 آبان.xlsx', apply=True)
        ads = Advertisement.query.order_by(Advertisement.id).all()
        self.assertEqual(len(ads), 2)
        self.assertNotEqual(ads[0].ma_reference, ads[1].ma_reference)
        self.assertEqual((result['source_ids'], result['repeated_source_rows']), (1, 1))
        for ad in ads:
            self.assertIn('شناسه آگهی در منبع: 2318993', ad.notes)
            self.assertIn('Sheet1', ad.notes)
        db.session.rollback()
        self.assertTrue(import_rows(rows, 'administrator', 'renamed.xlsx', apply=True)['already_imported'])
        self.assertEqual(Advertisement.query.count(), 2)

    def test_revision_cannot_be_imported_over_previous_workbook(self):
        import_rows(self.rows(), 'administrator', 'source.xlsx', apply=True)
        with self.assertRaisesRegex(ValueError, 'different workbook'):
            import_rows([dict(self.rows()[0], source_id=2318993)], 'administrator', '12 آبان.xlsx', apply=True)
        db.session.rollback()
        self.assertEqual(Advertisement.query.count(), 1)

    def test_new_command_dry_run_rolls_back(self):
        with patch('sobhe.workbook_import.read_rows', return_value=[dict(self.rows()[0], source_id=2318993)]):
            result = self.app.test_cli_runner().invoke(args=[
                'import-12-aban', str(__file__), '--username', 'administrator'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('DRY RUN:', result.output)
        self.assertEqual(Advertisement.query.count(), 0)

    def test_incremental_import_skips_legacy_rows_and_keeps_old_history(self):
        old_rows = [*self.rows(), dict(self.rows()[0], row=3, date='2026-10-26')]
        import_rows(old_rows, 'administrator', 'source.xlsx', apply=True)
        # Match an instance imported by the original version without row fingerprints.
        event = AuditEvent.query.filter_by(action='workbook_import').one()
        detail = json.loads(event.detail)
        detail.pop('row_counts')
        event.detail = json.dumps(detail)
        db.session.commit()
        old_ids = [(ad.id, ad.ma_reference, ad.version, ad.notes) for ad in Advertisement.query.all()]
        db.session.rollback()
        rows = [dict(self.rows()[0], source_id=2318993),
                dict(self.rows()[0], row=3, source_id=2318993, date='2026-11-09')]
        result = import_rows(rows, 'administrator', '12 آبان.xlsx', apply=True, incremental=True)
        self.assertEqual((result['advertisement'], result['skipped'], result['source_rows']), (1, 1, 2))
        self.assertEqual(Advertisement.query.count(), 3)
        for ad_id, reference, version, notes in old_ids:
            ad = db.session.get(Advertisement, ad_id)
            self.assertEqual((ad.ma_reference, ad.version, ad.notes), (reference, version, notes))
        db.session.rollback()
        reordered = [dict(row, row=10 + index) for index, row in enumerate(reversed(rows))]
        repeat = import_rows(reordered, 'administrator', 'renamed.xlsx', apply=True, incremental=True)
        self.assertEqual((repeat['advertisement'], repeat['skipped']), (0, 2))
        self.assertTrue(repeat['already_imported'])
        self.assertEqual(Advertisement.query.count(), 3)

    def test_incremental_dry_run_does_not_persist_data_or_audit(self):
        import_rows(self.rows(), 'administrator', 'source.xlsx', apply=True)
        revision = self.period.revision
        db.session.rollback()
        rows = [dict(self.rows()[0], source_id=123), dict(self.rows()[0], row=3, source_id=456, date='2026-11-09')]
        result = import_rows(rows, 'administrator', '12 آبان.xlsx', incremental=True)
        self.assertEqual((result['advertisement'], result['skipped']), (1, 1))
        self.assertEqual(Advertisement.query.count(), 1)
        self.assertEqual(AuditEvent.query.filter_by(action='workbook_import').count(), 1)
        self.assertEqual(self.period.revision, revision)

    def test_incremental_preserves_identical_publication_row_multiplicity(self):
        import_rows(self.rows(), 'administrator', 'source.xlsx', apply=True)
        rows = [dict(self.rows()[0], source_id=123), dict(self.rows()[0], row=3, source_id=123)]
        result = import_rows(rows, 'administrator', '12 آبان.xlsx', incremental=True, apply=True)
        self.assertEqual((result['advertisement'], result['skipped']), (1, 1))
        repeat = import_rows(rows, 'administrator', '12 آبان.xlsx', incremental=True, apply=True)
        self.assertEqual((repeat['advertisement'], repeat['skipped']), (0, 2))
        self.assertEqual(Advertisement.query.count(), 2)

    def test_incremental_remembers_rows_after_edit_and_void(self):
        import_rows(self.rows(), 'administrator', 'source.xlsx', apply=True)
        ad = Advertisement.query.one()
        ad.volume_units = 999
        ad.voided_at = datetime(2026, 11, 4)
        db.session.commit()
        result = import_rows([dict(self.rows()[0], source_id=123)], 'administrator', '12 آبان.xlsx',
                             incremental=True, apply=True)
        self.assertEqual((result['advertisement'], result['skipped']), (0, 1))
        self.assertEqual(Advertisement.query.count(), 1)
        self.assertEqual(Advertisement.query.one().volume_units, 999)

    def test_new_command_defaults_to_incremental_for_existing_import(self):
        import_rows(self.rows(), 'administrator', 'source.xlsx', apply=True)
        with patch('sobhe.workbook_import.read_rows', return_value=[dict(self.rows()[0], source_id=123)]):
            result = self.app.test_cli_runner().invoke(args=[
                'import-12-aban', str(__file__), '--username', 'administrator'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('"skipped": 1', result.output)
        self.assertIn('"advertisement": 0', result.output)

    def test_new_command_finds_bundled_workbook_without_path(self):
        workbook = self.root / unicodedata.normalize('NFD', '12 آبان.xlsx')
        workbook.write_bytes(b'fixture')
        with patch('sobhe.workbook_import.PROJECT_ROOT', self.root), patch(
                'sobhe.workbook_import.read_rows', return_value=self.rows()) as reader:
            result = self.app.test_cli_runner().invoke(args=['import-12-aban', '--username', 'administrator'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(reader.call_args.args[0].read_bytes(), b'fixture')
        self.assertIn('DRY RUN:', result.output)
        self.assertEqual(Advertisement.query.count(), 0)

    def test_explicit_composed_filename_resolves_decomposed_filename(self):
        decomposed = unicodedata.normalize('NFD', '12 آبان.xlsx')
        workbook = self.root / decomposed
        workbook.write_bytes(b'fixture')
        # Simulate Linux's exact filename matching even when tests run on macOS.
        with patch('sobhe.workbook_import.Path.is_file', autospec=True,
                   side_effect=lambda path: path.name == decomposed and path.exists()):
            resolved = resolve_workbook(self.root / '12 آبان.xlsx', 'import-12-aban')
        self.assertEqual(resolved.read_bytes(), b'fixture')

    def test_workbook_resolution_rejects_missing_file(self):
        with self.assertRaisesRegex(ValueError, 'Workbook not found'):
            resolve_workbook(self.root / 'missing.xlsx', 'import-12-aban')

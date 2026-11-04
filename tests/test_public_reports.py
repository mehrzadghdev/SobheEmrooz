"""Public cards, lists, drilldowns, and exports must describe the same records."""
import io
import re
from contextlib import contextmanager
from datetime import date, datetime
from html import unescape
from urllib.parse import parse_qs, urlsplit

from flask import template_rendered
from openpyxl import load_workbook

from sobhe.constants import COLUMNS, DATE_FIELDS
from sobhe.models import Subject
from sobhe.services import boxes
from tests.support import AppTestCase


class PublicReportTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.public = self.app.test_client()
        self.subject = Subject.query.filter_by(name='مناقصه').one()
        self.other_subject = Subject.query.filter_by(name='مزایده').one()
        self.first = self.add_ad(
            0, '1.25', ma_reference=6101, allocation_date=date(2026, 10, 1),
            created_at=datetime(2026, 9, 30, 21), approval_date=date(2026, 10, 4),
            proposed_date=date(2026, 10, 5), final_print_date=date(2026, 10, 6),
            letter_date=date(2026, 9, 29), subject_id=self.subject.id,
            province='خراسان رضوی', letter_number='ABC/12', subject_description='ساخت پل',
            ad_text='متن پل 100%_تست', ad_type='ثبت شرکت', print_status='منتظر چاپ',
            amount=1200, approved_amount=1000)
        self.second = self.add_ad(
            4, '2.75', ma_reference=6202, allocation_date=date(2026, 10, 2),
            created_at=datetime(2026, 10, 1, 21), approval_date=date(2026, 10, 7),
            proposed_date=date(2026, 10, 8), final_print_date=date(2026, 10, 9),
            letter_date=date(2026, 9, 28), subject_id=self.other_subject.id,
            organization_id=self.other_organization.id, province='تهران',
            letter_number='XYZ/34', subject_description='ساخت جاده', ad_text='متن جاده',
            ad_type='دولتی، سفارشی', print_status='چاپ شده', amount=3400, approved_amount=3000)
        self.unapproved = self.add_ad(
            1, '0.50', ma_reference=6303, allocation_date=date(2026, 10, 3),
            created_at=datetime(2026, 10, 2, 21))
        self.voided = self.add_ad(
            ma_reference=6404, allocation_date=date(2026, 10, 1),
            voided_at=datetime(2026, 10, 4), amount=90000)
        self.records = [self.first, self.second, self.unapproved]

    @contextmanager
    def rendered(self):
        contexts = []

        def capture(sender, template, context, **extra):
            contexts.append(context)

        template_rendered.connect(capture, self.app)
        try:
            yield contexts
        finally:
            template_rendered.disconnect(capture, self.app)

    def assert_results(self, filters, expected, *, views=('ads', 'organizations')):
        for view in views:
            with self.subTest(view=view, filters=filters):
                args = {'year': 1405, 'view': view, **filters}
                with self.rendered() as contexts:
                    response = self.public.get('/reports', query_string=args)
                self.assertEqual(response.status_code, 200)
                context = contexts[-1]
                totals = (len(expected), sum(ad.volume_units or 0 for ad in expected),
                          sum(ad.amount or 0 for ad in expected))
                self.assertEqual(tuple(context['totals']), totals)
                cards = re.findall(r'<span class="stat">(.*?)</span>', response.text)
                self.assertEqual(cards, [f'{totals[0]:,}', f'{boxes(totals[1])} <small>کادر</small>',
                                         f'{totals[2]:,} <small>ریال</small>'])
                if view == 'ads':
                    self.assertEqual({ad.id for ad in context['rows']}, {ad.id for ad in expected})
                else:
                    grouped = context['grouped_organizations']
                    self.assertEqual(sum(row.count for row in grouped), totals[0])
                    self.assertEqual(sum(row.volume or 0 for row in grouped), totals[1])
                    self.assertEqual(sum(row.amount for row in grouped), totals[2])
                    self.assertEqual({row.id for row in grouped}, {ad.organization_id for ad in expected})
                export = self.public.get('/reports', query_string={**args, 'export': 'xlsx',
                                                                  'columns': ['ma_reference']})
                self.assertEqual(export.status_code, 200)
                values = list(load_workbook(io.BytesIO(export.data)).active.values)[1:]
                if view == 'ads':
                    self.assertEqual({row[0] for row in values}, {ad.ma_reference for ad in expected})
                else:
                    self.assertEqual(sum(row[1] for row in values), totals[0])
                    self.assertEqual(sum(row[3] for row in values), totals[2])

    def test_default_cards_lists_and_exports_include_unapproved_records(self):
        self.assert_results({}, self.records)
        self.assert_results({'organization_id': self.organization.id}, [self.first, self.unapproved])
        self.assert_results({'date_field': 'approval_date'}, [self.first, self.second])

    def test_all_ad_lists_show_id_subject_and_print_status_by_default(self):
        for path in ('/reports?year=1405', '/admin/advertisements'):
            with self.rendered() as contexts:
                response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertTrue({'ma_reference', 'subject', 'print_status'} <= set(contexts[-1]['selected_columns']))
            self.assertIn('مناقصه', response.text)
            self.assertIn('منتظر چاپ', response.text)
        for path in ('/?year=1405', f'/media/{self.first.outlet_id}?year=1405',
                     f'/admin/media/{self.first.outlet_id}?year=1405'):
            self.sign_in(admin=True)
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('<th>شناسه آگهی</th><th>موضوع</th>', response.text)
            self.assertIn('<th>وضعیت چاپ</th>', response.text)
            self.assertIn('مناقصه', response.text)
            self.assertNotIn('<footer', response.text)

    def test_every_numeric_filter_and_persian_digits(self):
        for prefix, low, high in (
            ('id', self.first.id, self.second.id), ('ma', 6101, 6202),
            ('amount', 1200, 3400), ('approved_amount', 1000, 3000),
        ):
            high_matches = [self.second, self.unapproved] if prefix in ('id', 'ma') else [self.second]
            self.assert_results({f'{prefix}_min': high}, high_matches)
            self.assert_results({f'{prefix}_max': low}, [self.first])
            self.assert_results({f'{prefix}_min': low, f'{prefix}_max': low}, [self.first])
        self.assert_results({'ma_min': '۶۱۰۱', 'ma_max': '۶۱۰۱', 'amount_min': '۱٬۲۰۰'}, [self.first])

    def test_every_catalog_text_and_multiselect_filter(self):
        filters = (
            {'outlet_id': self.first.outlet_id}, {'subject_id': self.subject.id},
            {'province': 'خراسان رضوی'}, {'letter_number': 'ABC'},
            {'subject_description': 'پل'}, {'ad_text': '100%_تست'},
            {'ad_type': ['ثبت شرکت', 'متفرقه']}, {'print_status': ['منتظر چاپ', 'عدم چاپ'], 'ma_max': 6202},
        )
        for args in filters:
            self.assert_results(args, [self.first])
        self.assert_results({'ad_type': ['ثبت شرکت', 'دولتی، سفارشی'],
                             'print_status': ['منتظر چاپ', 'چاپ شده']}, [self.first, self.second])
        self.assert_results({'organization_id': self.organization.id, 'outlet_id': self.first.outlet_id,
                             'subject_id': self.subject.id, 'ma_min': 6101, 'ma_max': 6101,
                             'amount_min': 1200, 'approved_amount_max': 1000,
                             'ad_text': 'پل', 'ad_type': 'ثبت شرکت', 'print_status': 'منتظر چاپ'}, [self.first])
        self.assert_results({'outlet_id': self.first.outlet_id, 'province': 'تهران'}, [])

    def test_every_date_basis_and_inclusive_bounds(self):
        for field in DATE_FIELDS:
            value = getattr(self.first, field)
            day = date(2026, 10, 1) if field == 'created_at' else value
            self.assert_results({'date_field': field, 'start': day.isoformat(), 'end': day.isoformat()},
                                [self.first])
        self.assert_results({'start': '1405/07/09', 'end': '1405/07/10'}, [self.first, self.second])
        self.assert_results({'start': '2026-10-04', 'end': '2026-10-04'}, [])

    def test_search_sort_pagination_and_export_use_all_matches(self):
        self.assert_results({'q': '۶۱۰۱'}, [self.first], views=('ads',))
        self.assert_results({'q': self.second.organization.name}, [self.second], views=('ads',))
        self.assert_results({'q': self.first.outlet.name}, [self.first], views=('ads',))
        self.assert_results({'q': self.subject.name}, [self.first], views=('ads',))
        self.assert_results({'q': '100%_تست'}, [self.first], views=('ads',))
        for reference in range(6500, 6518):
            self.add_ad(ma_reference=reference, volume='0.25', amount=100)
        for direction in ('asc', 'desc'):
            with self.rendered() as contexts:
                response = self.public.get('/reports', query_string={
                    'year': 1405, 'per_page': 10, 'page': 2, 'sort': 'ma_reference', 'direction': direction})
            self.assertEqual(response.status_code, 200)
            context = contexts[-1]
            self.assertEqual(tuple(context['totals']), (21, 900, 6400))
            self.assertEqual((context['pages'], context['page']), (3, 2))
            self.assertEqual(len(context['rows']), 10)
            ordered = sorted([ad.ma_reference for ad in self.records] + list(range(6500, 6518)),
                             reverse=direction == 'desc')
            self.assertEqual([ad.ma_reference for ad in context['rows']], ordered[10:20])
            exported = self.public.get('/reports', query_string={
                'year': 1405, 'per_page': 10, 'page': 2, 'sort': 'ma_reference', 'direction': direction,
                'columns': 'ma_reference', 'export': 'xlsx'})
            values = list(load_workbook(io.BytesIO(exported.data)).active.values)
            self.assertEqual([row[0] for row in values[1:]], ordered)

    def test_all_display_columns_render_and_export_with_optional_values_missing(self):
        with self.rendered() as contexts:
            response = self.public.get('/reports', query_string={'year': 1405, 'columns': list(COLUMNS)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(contexts[-1]['selected_columns'], list(COLUMNS))
        self.assertEqual(len(contexts[-1]['rows']), 3)
        exported = self.public.get('/reports', query_string={'year': 1405, 'columns': list(COLUMNS), 'export': 'xlsx'})
        values = list(load_workbook(io.BytesIO(exported.data)).active.values)
        self.assertEqual(values[0], tuple(COLUMNS.values()))
        self.assertEqual(len(values), 4)
        unapproved = dict(zip(COLUMNS, next(row for row in values[1:] if row[list(COLUMNS).index('ma_reference')] == 6303)))
        self.assertEqual(unapproved['approval_date'], '—')
        self.assertEqual(unapproved['allocation_date'], '1405/07/11')
        self.assertEqual(unapproved['volume_units'], '0.5')

    def test_year_selection_and_custom_ranges(self):
        previous = self.make_period(1404)
        prior = self.add_ad(period=previous, allocation_date=date(2026, 3, 20), amount=900)
        self.assert_results({}, self.records)
        self.assert_results({'year': 1404}, [prior])
        self.assert_results({'year': 1406}, [])
        self.assert_results({'start': '2026-03-20', 'end': '2026-10-01'}, [prior, self.first])

    def test_organization_drilldown_retains_matching_results(self):
        args = {'year': 1405, 'view': 'organizations', 'amount_min': 1000, 'amount_max': 2000,
                'ad_type': ['ثبت شرکت', 'متفرقه'], 'start': '2026-10-01', 'end': '2026-10-03'}
        response = self.public.get('/reports', query_string=args)
        links = re.findall(r'href="([^"]*organization_id[^"]*)"', response.text)
        self.assertEqual(len(links), 1)
        target = unescape(links[0])
        params = parse_qs(urlsplit(target).query)
        self.assertEqual(params['ad_type'], args['ad_type'])
        with self.rendered() as contexts:
            response = self.public.get(target)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(contexts[-1]['totals'][0], 1)
        self.assertEqual([ad.id for ad in contexts[-1]['rows']], [self.first.id])

import io
import re
from datetime import date, datetime

from openpyxl import load_workbook

from tests.support import AppTestCase


class PageSeparationTests(AppTestCase):
    @staticmethod
    def content(response):
        return re.search(r'<main[^>]*>(.*?)</main>', response.text, re.S).group(1)

    def test_public_content_is_identical_for_visitors_and_administrators(self):
        ad = self.add_ad(approval_date=date(2026, 10, 1))
        self.sign_in(admin=True)
        public = self.app.test_client()
        paths = ('/?year=1405&half=2', '/reports?year=1405&half=2',
                 '/reports?view=media&year=1405&half=2',
                 '/reports?view=organizations&year=1405&half=2',
                 f'/advertisements/{ad.id}', f'/media/{ad.outlet_id}?year=1405&half=2')
        for path in paths:
            with self.subTest(path=path):
                visitor, administrator = public.get(path), self.client.get(path)
                self.assertEqual(visitor.status_code, 200)
                self.assertEqual(administrator.status_code, 200)
                content = self.content(administrator)
                self.assertEqual(content, self.content(visitor))
                self.assertNotIn('data-delete-ad', content)
                self.assertNotIn('/admin/', content)
                self.assertIn('data-space="public"', administrator.text)

    def test_default_report_date_does_not_change_with_sign_in(self):
        self.add_ad()
        self.sign_in(admin=True)
        public = self.app.test_client()
        for client in (public, self.client):
            response = client.get('/reports?year=1405&half=2&export=xlsx')
            self.assertEqual(load_workbook(io.BytesIO(response.data)).active.max_row, 1)
        response = self.client.get('/admin/advertisements?year=1405&half=2&export=xlsx')
        self.assertEqual(load_workbook(io.BytesIO(response.data)).active.max_row, 2)

    def test_management_routes_require_the_appropriate_role(self):
        ad = self.add_ad()
        public = self.app.test_client()
        for path in ('/admin/advertisements', '/admin/advertisements/new',
                     f'/admin/advertisements/{ad.id}', f'/admin/media/{ad.outlet_id}'):
            self.assertEqual(public.get(path).status_code, 302)
        self.assertEqual(self.client.get('/admin/advertisements').status_code, 200)
        self.assertEqual(self.client.get(f'/admin/advertisements/{ad.id}').status_code, 200)
        self.assertEqual(self.client.get(f'/admin/advertisements/{ad.id}/edit').status_code, 403)
        self.assertEqual(self.client.post(f'/admin/advertisements/{ad.id}/void').status_code, 403)
        self.assertEqual(self.client.get(f'/admin/media/{ad.outlet_id}').status_code, 403)

    def test_voided_records_are_only_visible_in_the_admin_detail_page(self):
        ad = self.add_ad(voided_at=datetime.now(), void_reason='ثبت تکراری')
        self.sign_in(admin=True)
        self.assertEqual(self.client.get(f'/advertisements/{ad.id}').status_code, 404)
        response = self.client.get(f'/admin/advertisements/{ad.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn('ثبت تکراری', self.content(response))

    def test_sidebar_orders_admin_routes_first_and_uses_distinct_icons(self):
        self.sign_in(admin=True)
        html = self.client.get('/reports').text
        sidebar = html.split('<div class="sidebar-links">', 1)[1].split('<div class="sidebar-profile">', 1)[0]
        links = re.findall(r'<a class="side-link[^>]*href="([^"]+)"', sidebar)
        self.assertEqual(links[:3], ['/admin/advertisements', '/admin/catalog/outlets', '/admin/catalog/organizations'])
        self.assertEqual(links[7:], ['/', '/reports', '/reports?view=media', '/reports?view=organizations'])
        self.assertIn('<span class="nav-label">رسانه‌ها</span>', sidebar)
        self.assertNotIn('مدیریت رسانه‌ها', sidebar)
        icons = re.findall(r'<svg.*?</svg>', sidebar, re.S)
        self.assertEqual(len(icons), len(set(icons)))

    def test_organization_drilldown_preserves_the_report_filters(self):
        self.add_ad(approval_date=date(2026, 10, 1), amount=2500)
        response = self.client.get('/reports', query_string={
            'year': 1405, 'half': 2, 'view': 'organizations', 'amount_min': 2000,
            'date_field': 'approval_date', 'start': '2026-10-01', 'end': '2026-10-02',
        })
        self.assertEqual(response.status_code, 200)
        content = self.content(response)
        self.assertIn('view=ads', content)
        self.assertIn('amount_min=2000', content)
        self.assertIn(f'organization_id={self.organization.id}', content)

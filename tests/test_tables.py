from flask import template_rendered
from sobhe.models import AuditEvent, db
from tests.support import AppTestCase


class TableTests(AppTestCase):
    def render_context(self, url):
        captured = []
        def receiver(sender, template, context, **extra):
            captured.append(context)
        with template_rendered.connected_to(receiver, self.app):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return captured[-1]

    def test_ads_paginate_search_and_sort_across_pages(self):
        for index in range(19):
            self.add_ad(volume=index + 1, notes='needle' if index == 0 else 'ordinary')
        context = self.render_context('/admin/advertisements')
        self.assertEqual(len(context['rows']), 15)
        self.assertEqual(context['pages'], 2)
        context = self.render_context('/admin/advertisements?sort=volume_units&direction=asc&page=2')
        self.assertEqual([row.volume_units for row in context['rows']], [1600, 1700, 1800, 1900])
        context = self.render_context('/admin/advertisements?q=needle&page=99')
        self.assertEqual(len(context['rows']), 1)
        self.assertEqual(context['page'], 1)
        context = self.render_context('/admin/advertisements?sort=organization&direction=desc')
        self.assertEqual(len(context['rows']), 15)
        self.assertEqual(self.client.get('/admin/advertisements?sort=invalid').status_code, 400)

    def test_audit_search_sort_and_default_page_size(self):
        self.sign_in(admin=True)
        db.session.add_all([AuditEvent(action='table_test', target=str(i), detail='unique needle' if i == 0 else '') for i in range(18)])
        db.session.commit()
        context = self.render_context('/admin/audit')
        self.assertEqual(len(context['events']), 15)
        context = self.render_context('/admin/audit?q=unique+needle&sort=target&direction=asc')
        self.assertEqual(len(context['events']), 1)

    def test_dashboard_percentage_colors_follow_exact_group_targets(self):
        self.add_ad(index=0, volume=70)
        self.add_ad(index=4, volume=30)
        response = self.client.get('/')
        self.assertIn('percentage-status warning', response.text)
        self.assertIn('percentage-status success', response.text)

    def test_search_treats_sql_wildcards_as_literal(self):
        self.add_ad(notes='100%')
        self.add_ad(notes='ordinary')
        self.assertEqual(len(self.render_context('/admin/advertisements?q=%25')['rows']), 1)

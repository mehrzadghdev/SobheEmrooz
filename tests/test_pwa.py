"""Public install resources must never depend on accounts or advertisement data."""
import io
import json
import re
from unittest.mock import patch

from PIL import Image

from sobhe import create_app
from sobhe.models import db
from tests.support import AppTestCase


class PWATests(AppTestCase):
    def test_manifest_and_icons_are_installable_under_the_whole_application(self):
        response = self.client.get('/manifest.webmanifest')
        self.assertEqual(response.mimetype, 'application/manifest+json')
        self.assertEqual(response.headers['Cache-Control'], 'no-cache')
        manifest = response.json
        self.assertEqual((manifest['id'], manifest['start_url'], manifest['scope']), ('/', '/', '/'))
        self.assertEqual((manifest['lang'], manifest['dir'], manifest['display']), ('fa-IR', 'rtl', 'standalone'))
        self.assertEqual(manifest['theme_color'], '#da1214')
        self.assertEqual({icon['purpose'] for icon in manifest['icons']}, {'any', 'maskable'})
        for icon in manifest['icons']:
            with self.client.get(icon['src']) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Image.open(io.BytesIO(response.data)).size,
                                 tuple(map(int, icon['sizes'].split('x'))))
        with self.client.get('/static/icons/apple-touch-icon.png') as response:
            self.assertEqual(Image.open(io.BytesIO(response.data)).size, (180, 180))
        with self.client.get('/favicon.ico') as response:
            self.assertEqual(response.mimetype, 'image/png')

    def test_offline_screen_and_metadata_do_not_query_accounts_or_write_cookies(self):
        self.sign_in(admin=True)
        with patch.object(db.session, 'get', side_effect=AssertionError('PWA resource queried the database')):
            for path in ('/offline', '/manifest.webmanifest', '/service-worker.js', '/favicon.ico'):
                with self.client.get(path) as response:
                    self.assertEqual(response.status_code, 200, path)
                    self.assertNotIn('Set-Cookie', response.headers)
                    self.assertNotIn('Cookie', response.headers.get('Vary', ''))

    def test_offline_screen_is_identical_for_public_operator_and_admin(self):
        public = self.app.test_client().get('/offline')
        operator = self.client.get('/offline')
        self.sign_in(admin=True)
        admin = self.client.get('/offline')
        self.assertEqual(public.data, operator.data)
        self.assertEqual(public.data, admin.data)
        self.assertNotIn('csrf_token', admin.text)
        self.assertNotIn(self.admin.name, admin.text)
        self.assertIn('اتصال اینترنت برقرار نیست', admin.text)
        self.assertEqual(admin.headers['Cache-Control'], 'public, max-age=0, must-revalidate')

    def test_public_pwa_resources_work_before_database_migration(self):
        app = create_app({'TESTING': True, 'SECRET_KEY': 'unmigrated',
                          'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'}, instance_path=str(self.root / 'empty'))
        client = app.test_client()
        for path in ('/offline', '/manifest.webmanifest', '/service-worker.js'):
            self.assertEqual(client.get(path).status_code, 200, path)

    def test_worker_only_precaches_existing_public_resources(self):
        response = self.client.get('/service-worker.js')
        self.assertEqual(response.mimetype, 'application/javascript')
        self.assertEqual(response.headers['Cache-Control'], 'no-cache')
        self.assertEqual(response.headers['Service-Worker-Allowed'], '/')
        self.assertNotIn('{{', response.text)
        paths = json.loads(re.search(r'const PUBLIC_PATHS = new Set\((\[.*?\])\);', response.text).group(1))
        self.assertTrue(paths)
        self.assertTrue(all(path.startswith('/static/') or path == '/manifest.webmanifest' for path in paths))
        for path in paths:
            with self.client.get(path) as asset:
                self.assertEqual(asset.status_code, 200, path)

    def test_resources_respect_reverse_proxy_prefixes_and_data_pages_remain_uncached(self):
        prefix = {'SCRIPT_NAME': '/sobhe'}
        manifest = self.client.get('/manifest.webmanifest', environ_overrides=prefix).json
        self.assertEqual((manifest['scope'], manifest['start_url']), ('/sobhe/', '/sobhe/'))
        self.assertTrue(all(icon['src'].startswith('/sobhe/static/') for icon in manifest['icons']))
        worker = self.client.get('/service-worker.js', environ_overrides=prefix)
        self.assertEqual(worker.headers['Service-Worker-Allowed'], '/sobhe/')
        for path in ('/', '/reports', '/advertisements/new'):
            response = self.client.get(path)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertIn('rel="manifest"', response.text)
            self.assertIn('id="navigation-progress"', response.text)

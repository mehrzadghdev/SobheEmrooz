"""Disposable SQLite fixtures; never reads or writes the application's instance."""
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image
from werkzeug.security import generate_password_hash

from sobhe import create_app
from sobhe.commands import migrate
from sobhe.constants import CATEGORIES
from sobhe.models import *
from sobhe.membership import enroll_period
from sobhe.services import period_bounds, units


class AppTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = generate_password_hash('test-password-123')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sobhe-tests-')
        self.root = Path(self.temp.name)
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-secret', 'CSRF_ENABLED': False,
                               'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + str(self.root / 'test.db'),
                               'STORAGE_ROOT': str(self.root / 'storage')}, instance_path=str(self.root))
        self.context = self.app.app_context()
        self.context.push()
        migrate()
        self.admin = User(username='administrator', name='مدیر آزمایش', role='admin', active=True, password_hash=self.password_hash)
        self.operator = User(username='operator', name='کارشناس آزمایش', role='operator', active=True, password_hash=self.password_hash)
        self.outlets = [Outlet(name=name, active=True, category=CATEGORIES[index // 2])
                        for index, name in enumerate(('صبح امروز', 'شهرآرا', 'محلی دوم', 'محلی سوم', 'خراسان', 'اطلاعات'))]
        self.organization = Organization(name='راه و شهرسازی', active=True)
        self.other_organization = Organization(name='شهرداری', active=True)
        db.session.add_all([self.admin, self.operator, self.organization, self.other_organization] + self.outlets)
        db.session.commit()
        self.period = self.make_period(1405, 2)
        self.client = self.app.test_client()
        self.sign_in()
        self.next_ma = 5000

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.temp.cleanup()

    def make_period(self, year, half):
        period = Period(year=year, half=half, active=True)
        db.session.add(period)
        db.session.flush()
        enroll_period(period)
        db.session.commit()
        return period

    def sign_in(self, admin=False, client=None):
        user = self.admin if admin else self.operator
        with (client or self.client).session_transaction() as session:
            session['user_id'] = user.id
            session['auth_version'] = user.auth_version

    def add_ad(self, index=0, volume=10, period=None, **kwargs):
        period = period or self.period
        self.next_ma += 1
        values = dict(ma_reference=self.next_ma, organization_id=self.organization.id,
                      outlet_id=self.outlets[index].id, period_id=period.id, volume_units=units(volume, positive=True),
                      allocation_date=period_bounds(period.year, period.half)[0], creator_id=self.operator.id)
        values.update(kwargs)
        ad = Advertisement(**values)
        db.session.add(ad)
        db.session.commit()
        return ad

    def volumes(self, values):
        for index, value in enumerate(values):
            if value:
                self.add_ad(index, value)

    def data(self, **overrides):
        values = {'ma_reference': '1002', 'organization_id': str(self.organization.id),
                  'outlet_id': str(self.outlets[0].id), 'allocation_date': '2026-10-01', 'volume': '10',
                  'print_status': 'منتظر چاپ', 'override_confirmed': '1'}
        values.update(overrides)
        return values

    def preview(self, data=None, ad=None, client=None):
        data = data or self.data()
        args = {key: data[key] for key in ('volume', 'allocation_date', 'outlet_id')}
        if ad:
            args['exclude_id'] = ad.id
        response = (client or self.client).get('/api/allocation-preview', query_string=args)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json

    def submit(self, data=None, ad=None, **overrides):
        data = data or self.data(**overrides)
        data['preview_token'] = self.preview(data, ad)['token']
        if ad:
            data['version'] = str(ad.version)
        return self.client.post(f'/admin/advertisements/{ad.id}/edit' if ad else '/admin/advertisements/new', data=data)


def png_bytes():
    content = io.BytesIO()
    Image.new('RGB', (3, 3), '#166345').save(content, 'PNG')
    return content.getvalue()

import json
from datetime import date
from sobhe import allocations
from sobhe.commands import migrate
from sobhe.directive import (DirectiveRecord, eligibility, rank, quote, priority,
                             working_days, next_month, PRINT_RATES, ONLINE_RATES)
from sobhe.models import db, SchemaVersion
from tests.support import AppTestCase


def profile(**overrides):
    data=dict(format='daily',scope='provincial',frequency='daily',hq='خراسان رضوی',city='مشهد',
              score='60',regularity='80',pages='8',width='25',height='35',coverage='61',website='https://example.ir',
              license=True,application=True,insurance=True,verified=True,owner_approval=True,history_verified=True,changed_on='2020-01-01',owner='مالک')
    data.update(overrides)
    return data


class DirectiveTests(AppTestCase):
    def put(self,kind,ref,data):
        row=DirectiveRecord(kind=kind,reference=str(ref),payload=json.dumps(data))
        db.session.add(row); db.session.commit()
        return row

    def test_rank_boundaries_and_eligibility(self):
        for score,expected in ((80,1),('79.99',2),(60,2),('59.99',3),(50,3),(40,4),('39.99',5)):
            self.assertEqual(rank(profile(score=score)),expected)
        self.assertEqual(eligibility(profile()),[])
        self.assertIn('گستره توزیع',eligibility(profile(coverage='60')))
        self.assertTrue(eligibility(profile(insurance=False)))
        self.assertEqual(eligibility(profile(format='website',score='20',top_five=True)),[])
        self.assertTrue(eligibility(profile(format='website',hq='تهران',score='49.99')))

    def test_tariff_every_print_cell_and_online_rank(self):
        for r,score in enumerate((80,60,50,40,30)):
            for p in range(4):
                self.assertEqual(quote(dict(volume=1,page=p),profile(score=score))['total'],PRINT_RATES[r][p])
        for r,score in enumerate((80,65,50)):
            self.assertEqual(quote(dict(volume=10),profile(format='website',score=score))['total'],ONLINE_RATES[r])

    def test_modifiers_and_approved_adjustment(self):
        result=quote(dict(volume='1.5',page=3,monochrome=True,repeats=2),profile(score=80,frequency='weekly'))
        self.assertEqual(result['total'],20160000)
        self.assertEqual(quote(dict(volume=1,billing='company',named_request=True),profile())['total'],6000000)
        self.assertEqual(quote(dict(volume=1,billing='civil',logo=True),profile(score=50))['total'],2100000)
        self.assertEqual(quote(dict(volume=1,billing='criminal',quota_media=True,monochrome=True),profile())['total'],2100000)
        with self.assertRaises(ValueError): quote(dict(volume=1,adjustment=10),profile())
        with self.assertRaises(ValueError): quote(dict(volume=1,billing='company'),profile())

    def test_jurisdiction_and_calendar(self):
        self.assertEqual(priority(profile(),{'province':'خراسان رضوی','city':'مشهد'}),1)
        self.assertEqual(priority(profile(scope='national'),{'province':'خراسان رضوی'}),2)
        self.assertIsNone(priority(profile(format='website',hq='تهران'),{'province':'خراسان رضوی'}))
        self.assertEqual(working_days(date(2026,11,5),date(2026,11,9),{'holidays':'2026-11-07'}),2)
        self.assertGreater(next_month(date(2026,11,5)),date(2026,11,5))

    def test_schema_six_idempotent(self):
        self.assertEqual(db.session.get(SchemaVersion,6).version,6)
        self.assertIn('already current',migrate())

    def test_policy_requires_verified_profiles_and_binds_preview(self):
        config=dict(active=True,province='خراسان رضوی',city='مشهد',target='65')
        self.put('settings','office',config)
        for outlet in self.outlets:
            self.put('profile',outlet.id,profile(scope='provincial' if outlet.category=='local' else 'national'))
        first=allocations.preview(self.period,100)
        self.assertTrue(first['directive'])
        self.assertEqual(first['groups']['local']['target_percent'],65)
        row=DirectiveRecord.query.filter_by(kind='profile',reference=str(first['recommended']['outlet_id'])).one()
        data=row.data; data['verified']=False; row.payload=json.dumps(data);row.version+=1;db.session.commit()
        second=allocations.preview(self.period,100)
        self.assertNotEqual(first['fingerprint'],second['fingerprint'])
        self.assertNotEqual(first['recommended']['outlet_id'],second['recommended']['outlet_id'])

    def test_workspace_permissions_and_render(self):
        self.assertEqual(self.client.get('/admin/directive?kind=settings').status_code,403)
        self.assertEqual(self.client.get('/admin/directive?kind=order').status_code,200)
        self.sign_in(admin=True)
        for kind in ('settings','profile','order','placement','sanction','tariff','finding','obligation'):
            with self.subTest(kind=kind): self.assertEqual(self.client.get('/admin/directive?kind='+kind).status_code,200)

    def test_activation_requires_confirmations(self):
        self.sign_in(admin=True)
        response=self.client.post('/admin/directive',data=dict(kind='settings',reference='office',active='1',target=65))
        self.assertEqual(response.status_code,200)
        self.assertIsNone(DirectiveRecord.query.filter_by(kind='settings').first())

    def test_order_transition_allocation_and_certificate(self):
        from sobhe.models import Advertisement
        self.sign_in(admin=True)
        self.put('settings','office',dict(active=True,province='خراسان رضوی',city='مشهد',target='65',tariff_confirmed=True))
        self.put('profile',self.outlets[0].id,profile())
        row=self.put('order','order-one',dict(state='reviewed',organization=self.organization.name,
            subject='مناقصه',representative='کارشناس',text='متن',submitted_on='2026-11-01',requested_on='2026-11-10',decision='تصمیم مدیر'))
        placement=self.put('placement','placement-one',dict(order_id=str(row.id),outlet_id=str(self.outlets[0].id),
            volume='2',page='3',repeats='1',billing='government',actual_volume='2',actual_on='2026-11-10',publication_evidence='نسخه چاپ',quote=quote({'volume':2},profile())))
        data=row.data
        response=self.client.post('/admin/directive',data=dict(data,kind='order',reference=row.reference,version=row.version,state='allocated'))
        self.assertEqual(response.status_code,302,response.text)
        self.assertEqual(Advertisement.query.count(),1)
        self.assertEqual(len(db.session.get(DirectiveRecord,placement.id).data['ad_ids']),1)
        for state,values in [('dispatched',dict(official_reference='123',dispatch_evidence='نامه')),('published',dict(publication_evidence='نسخه چاپ')),
                             ('verified',dict(invoice_on='2026-11-11')),('certified',dict(certificate_on='2026-11-12',certificate_reference='456')),
                             ('paid',dict(paid_on='2026-11-15',payment_reference='receipt'))]:
            db.session.expire_all();row=db.session.get(DirectiveRecord,row.id)
            data={k:v for k,v in row.data.items() if not isinstance(v,(dict,list))}
            response=self.client.post('/admin/directive',data=dict(data,**values,kind='order',reference=row.reference,version=row.version,state=state))
            self.assertEqual(response.status_code,302,(state,response.text))
        db.session.expire_all()
        self.assertEqual(db.session.get(DirectiveRecord,row.id).data['state'],'paid')

    def test_private_word_evidence_and_bad_upload_rollback(self):
        import io,zipfile
        self.sign_in(admin=True)
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('[Content_Types].xml','<Types/>')
            archive.writestr('word/document.xml','<document/>')
        response=self.client.post('/admin/directive',data=dict(kind='order',evidence=(io.BytesIO(buffer.getvalue()),'order.docx')))
        self.assertEqual(response.status_code,302)
        row=DirectiveRecord.query.filter_by(kind='order').one()
        path=f'/admin/directive/{row.id}/evidence/0'
        with self.client.get(path) as response:
            self.assertEqual(response.status_code,200)
        self.assertEqual(self.app.test_client().get(path).status_code,403)
        response=self.client.post('/admin/directive',data=dict(kind='order',evidence=(io.BytesIO(b'bad'),'bad.docx')))
        self.assertEqual(response.status_code,200)
        self.assertEqual(DirectiveRecord.query.filter_by(kind='order').count(),1)

    def test_text_and_area_equivalents(self):
        self.assertEqual(quote(dict(volume=1,billing='civil',character_count=601),profile(score=50))['total'],2100000)
        self.assertEqual(quote(dict(volume=1,billing='civil',word_count=101,graphic_boxes=1),profile(score=50))['total'],3150000)
        self.assertEqual(quote(dict(volume=1,area=56,page=3),profile(score=80,frequency='weekly'))['total'],16800000)

    def test_quarterly_export_validates_period(self):
        self.add_ad()
        response=self.client.get('/reports/directive-quarter?year=1405&quarter=1')
        self.assertEqual(response.status_code,200)
        response.close()
        self.assertEqual(self.client.get('/reports/directive-quarter?year=1405&quarter=5').status_code,400)

    def test_tariff_api_authentication_and_values(self):
        self.put('profile',self.outlets[0].id,profile())
        path=f'/api/tariff-preview?outlet_id={self.outlets[0].id}&volume=2&page=0'
        response=self.client.get(path)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['total'],146000000)
        self.assertEqual(self.app.test_client().get(path).status_code,403)
        self.assertEqual(self.client.get(path.replace('volume=2','volume=bad')).status_code,400)

    def test_directive_post_csrf_is_enforced(self):
        self.sign_in(admin=True)
        self.app.config['CSRF_ENABLED']=True
        self.assertEqual(self.client.post('/admin/directive',data=dict(kind='settings')).status_code,400)

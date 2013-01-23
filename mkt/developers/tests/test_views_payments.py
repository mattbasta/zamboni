import json

from django.core.exceptions import ObjectDoesNotExist

import mock
from nose.tools import eq_, raises
from pyquery import PyQuery as pq

import amo
import amo.tests
from amo.urlresolvers import reverse
from addons.models import Addon, AddonCategory, AddonDeviceType, AddonUser, Category
from market.models import Price
from users.models import UserProfile

import mkt
from mkt.developers.models import AddonPaymentAccount, PaymentAccount, SolitudeSeller
from mkt.site.fixtures import fixture
from mkt.webapps.models import AddonExcludedRegion as AER, ContentRating


def setup_payment_account(app, user):
    seller = SolitudeSeller.objects.create(user=user, uuid='uid')
    payment = PaymentAccount.objects.create(user=user, solitude_seller=seller)
    return AddonPaymentAccount.objects.create(addon=app,
        product_uri='//%s/' % app.pk, payment_account=payment, set_price=1)



class InappTest(amo.tests.TestCase):

    def setUp(self):
        self.create_switch('in-app-payments')
        self.app = Addon.objects.get(pk=337141)
        self.user = UserProfile.objects.get(pk=31337)
        self.other = UserProfile.objects.get(pk=999)
        self.login(self.user)
        setup_payment_account(self.app, self.user)
        self.url = reverse('mkt.developers.apps.in_app_config',
                           args=[self.app.app_slug])

    def set_mocks(self, solitude):
        get = mock.Mock()
        get.get_object_or_404.return_value = {'resource_pk': 'some-key',
                                              'secret': 'shhh!'}
        post = mock.Mock()
        post.return_value = get
        solitude.api.generic.product = post


@mock.patch('mkt.developers.views_payments.client')
class TestInappConfig(InappTest):
    fixtures = fixture('webapp_337141', 'user_999')

    @raises(ObjectDoesNotExist)
    def test_not_seller(self, solitude):
        post = mock.Mock()
        post.side_effect = ObjectDoesNotExist
        solitude.api.generic.product = post
        eq_(self.client.get(self.url).status_code, 404)

    def test_key_generation(self, solitude):
        self.set_mocks(solitude)
        self.client.post(self.url, {})
        args = solitude.api.generic.product().patch.call_args
        assert 'secret' in args[1]['data']

    def test_logged_out(self, solitude):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.url))

    def test_different(self, solitude):
        self.login(self.other)
        eq_(self.client.get(self.url).status_code, 403)

    def test_developer(self, solitude):
        self.login(self.other)
        AddonUser.objects.create(addon=self.app, user=self.other,
                                 role=amo.AUTHOR_ROLE_DEV)
        # Developer can read, but not reset.
        eq_(self.client.get(self.url).status_code, 200)
        eq_(self.client.post(self.url).status_code, 403)


@mock.patch('mkt.developers.views_payments.client')
class TestInappSecret(InappTest):
    fixtures = fixture('webapp_337141', 'user_999')

    def setUp(self):
        super(TestInappSecret, self).setUp()
        self.url = reverse('mkt.developers.apps.in_app_secret',
                           args=[self.app.app_slug])

    def test_show_secret(self, solitude):
        self.set_mocks(solitude)
        resp = self.client.get(self.url)
        eq_(resp.content, 'shhh!')

    def test_logged_out(self, solitude):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.url))

    def test_different(self, solitude):
        self.client.login(username='regular@mozilla.com', password='password')
        eq_(self.client.get(self.url).status_code, 403)

    def test_developer(self, solitude):
        self.set_mocks(solitude)
        self.login(self.other)
        AddonUser.objects.create(addon=self.app, user=self.other,
                                 role=amo.AUTHOR_ROLE_DEV)
        resp = self.client.get(self.url)
        eq_(resp.content, 'shhh!')


class TestPayments(amo.tests.TestCase):
    fixtures = ['base/apps', 'base/users', 'webapps/337141-steamcube',
                'market/prices']

    def setUp(self):
        self.webapp = self.get_webapp()
        AddonDeviceType.objects.create(
            addon=self.webapp, device_type=amo.DEVICE_GAIA.id)
        self.url = self.webapp.get_dev_url('payments')
        self.username = 'admin@mozilla.com'
        assert self.client.login(username=self.username, password='password')
        self.price = Price.objects.filter()[0]
        self.patch = mock.patch('mkt.developers.models.client')
        self.sol = self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def get_webapp(self):
        return Addon.objects.get(pk=337141)

    def get_region_list(self):
        return list(AER.objects.values_list('region', flat=True))

    def get_postdata(self, base):
        extension = {'regions': self.get_region_list(),
                     'other_regions': 'on',
                     'free_platforms': ['free-%s' % dt.class_name for dt in
                                        self.webapp.device_types],
                     'paid_platforms': ['paid-%s' % dt.class_name for dt in
                                        self.webapp.device_types],}
        base.update(extension)
        return base

    def test_free(self):
        res = self.client.post(
            self.url, self.get_postdata({'toggle-paid': 'free'}))
        self.assert3xx(res, self.url)
        eq_(self.get_webapp().premium_type, amo.ADDON_FREE)

    def test_premium_passes(self):
        self.webapp.update(premium_type=amo.ADDON_FREE)
        res = self.client.post(
            self.url, self.get_postdata({'toggle-paid': 'paid'}))
        self.assert3xx(res, self.url)
        eq_(self.get_webapp().premium_type, amo.ADDON_PREMIUM)

    def test_premium_in_app_passes(self):
        self.webapp.update(premium_type=amo.ADDON_FREE)
        res = self.client.post(
            self.url, self.get_postdata({'toggle-paid': 'paid'}))
        self.assert3xx(res, self.url)
        res = self.client.post(
            self.url, self.get_postdata({'allow_inapp': True,
                                         'price': self.price.pk}))
        self.assert3xx(res, self.url)
        eq_(self.get_webapp().premium_type, amo.ADDON_PREMIUM_INAPP)

    def test_later_then_free(self):
        self.webapp.update(premium_type=amo.ADDON_PREMIUM,
                           status=amo.STATUS_NULL,
                           highest_status=amo.STATUS_PENDING)
        res = self.client.post(
            self.url, self.get_postdata({'toggle-paid': 'free',
                                         'price': self.price.pk}))
        self.assert3xx(res, self.url)
        eq_(self.get_webapp().status, amo.STATUS_PENDING)

    def test_premium_price_initial_already_set(self):
        Price.objects.create(price='0.00')  # Make a free tier for measure.
        self.make_premium(self.webapp)
        r = self.client.get(self.url)
        eq_(pq(r.content)('select[name=price] option[selected]').attr('value'),
            str(self.webapp.premium.price.id))

    def test_premium_price_initial_use_default(self):
        Price.objects.create(price='10.00')  # Make one more tier.

        self.webapp.update(premium_type=amo.ADDON_FREE)
        res = self.client.post(
            self.url, self.get_postdata({'toggle-paid': 'paid'}), follow=True)
        pqr = pq(res.content)
        eq_(pqr('select[name=price] option[selected]').attr('value'),
            str(Price.objects.get(price='0.99').id))

    def test_associate_acct_to_app(self):
        # Set up Solitude return values.
        self.sol.get_product.return_value = {'meta': {'total_count': 0}}
        self.sol.post_product.return_value = {'resource_uri': 'gpuri'}
        self.sol.get_product_bango.return_value = {'meta': {'total_count': 0}}
        self.sol.post_product_bango.return_value = {
            'resource_uri': 'bpruri', 'bango_id': 123}

        # Set up an existing bank account.
        user = UserProfile.objects.get(email=self.username)
        amo.set_user(user)
        seller = SolitudeSeller.objects.create(
            resource_uri='/path/to/sel', user=user)
        acct = PaymentAccount.objects.create(
            user=user, uri='asdf', name='test', inactive=False,
            solitude_seller=seller, bango_package_id=123)

        # Associate account with app.
        res = self.client.post(
            self.url, self.get_postdata({'toggle-paid': 'paid',
                                         'price': self.price.pk,
                                         'accounts': acct.pk}), follow=True)
        self.assertNoFormErrors(res)
        eq_(res.status_code, 200)
        eq_(self.webapp.app_payment_account.payment_account.pk, acct.pk)


class TestRegions(amo.tests.TestCase):
    fixtures = ['base/apps', 'base/users', 'webapps/337141-steamcube']

    def setUp(self):
        self.webapp = self.get_webapp()
        AddonDeviceType.objects.create(
            addon=self.webapp, device_type=amo.DEVICE_GAIA.id)
        self.url = self.webapp.get_dev_url('payments')
        self.username = 'admin@mozilla.com'
        assert self.client.login(username=self.username, password='password')
        self.patch = mock.patch('mkt.developers.models.client')
        self.sol = self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def get_webapp(self):
        return Addon.objects.get(pk=337141)

    def get_dict(self, **kwargs):
        extension = {'regions': mkt.regions.REGION_IDS,
                     'other_regions': 'on',
                     'free_platforms': ['free-%s' % dt.class_name for dt in
                                        self.webapp.device_types]}
        extension.update(kwargs)
        return extension

    def get_excluded_ids(self):
        return sorted(AER.objects.filter(addon=self.webapp)
                                 .values_list('region', flat=True))

    def test_edit_other_categories_are_not_excluded(self):
        # Keep the category around for good measure.
        Category.objects.create(type=amo.ADDON_WEBAPP, slug='games')

        r = self.client.post(self.url, self.get_dict())
        self.assertNoFormErrors(r)
        eq_(AER.objects.count(), 0)

    def test_brazil_games_form_disabled(self):
        games = Category.objects.create(type=amo.ADDON_WEBAPP, slug='games')
        AddonCategory.objects.create(addon=self.webapp, category=games)

        r = self.client.get(self.url, self.get_dict())
        self.assertNoFormErrors(r)

        td = pq(r.content)('#regions')
        eq_(td.find('div[data-disabled]').attr('data-disabled'),
            '[%d]' % mkt.regions.BR.id)
        eq_(td.find('.note.disabled-regions').length, 1)

    def test_brazil_games_form_enabled_with_content_rating(self):
        rb = mkt.regions.BR.ratingsbodies[0]
        ContentRating.objects.create(
            addon=self.webapp, ratings_body=rb.id, rating=rb.ratings[0].id)

        games = Category.objects.create(type=amo.ADDON_WEBAPP, slug='games')
        AddonCategory.objects.create(addon=self.webapp, category=games)

        r = self.client.get(self.url)
        td = pq(r.content)('#regions')
        eq_(td.find('div[data-disabled]').attr('data-disabled'), '[]')
        eq_(td.find('.note.disabled-regions').length, 0)

    def test_brazil_other_cats_form_enabled(self):
        r = self.client.get(self.url)

        td = pq(r.content)('#regions')
        eq_(td.find('div[data-disabled]').attr('data-disabled'), '[]')
        eq_(td.find('.note.disabled-regions').length, 0)


class PaymentsBase(amo.tests.TestCase):
    fixtures = fixture('user_editor', 'user_999')

    def setUp(self):
        self.user = UserProfile.objects.get(pk=999)
        self.login(self.user)
        self.account = self.create()

    def create(self):
        # If user is defined on SolitudeSeller, why do we also need it on
        # PaymentAccount? Fewer JOINs.
        seller = SolitudeSeller.objects.create(user=self.user)
        return PaymentAccount.objects.create(user=self.user,
                                             solitude_seller=seller,
                                             uri='/bango/package/123',
                                             name="cvan's cnotes")


class TestPaymentAccountsAdd(PaymentsBase):
    # TODO: this test provides bare coverage and might need to be expanded.

    def setUp(self):
        super(TestPaymentAccountsAdd, self).setUp()
        self.url = reverse('mkt.developers.bango.add_payment_account')

    def test_login_required(self):
        self.client.logout()
        self.assertLoginRequired(self.client.post(self.url, data={}))

    @mock.patch('mkt.developers.models.client')
    def test_create(self, client):
        res = self.client.post(self.url, data={
            'bankAccountPayeeName': 'name',
            'companyName': 'company',
            'vendorName': 'vendor',
            'financeEmailAddress': 'a@a.com',
            'adminEmailAddress': 'a@a.com',
            'address1': 'address 1',
            'addressCity': 'city',
            'addressState': 'state',
            'addressZipCode': 'zip',
            'addressPhone': '123',
            'countryIso': 'BRA',
            'currencyIso': 'EUR',
            'bankAccountNumber': '123',
            'bankAccountCode': '123',
            'bankName': 'asd',
            'bankAddress1': 'address 2',
            'bankAddressZipCode': '123',
            'bankAddressIso': 'BRA',
            'account_name': 'account'
        })
        eq_(res.status_code, 302, res.content)
        eq_(PaymentAccount.objects.count(), 2)


class TestPaymentAccounts(PaymentsBase):

    def setUp(self):
        super(TestPaymentAccounts, self).setUp()
        self.url = reverse('mkt.developers.bango.payment_accounts')

    def test_login_required(self):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.url))

    def test_mine(self):
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        output = json.loads(res.content)
        eq_(output[0]['id'], self.account.pk)


class TestPaymentAccount(PaymentsBase):

    def setUp(self):
        super(TestPaymentAccount, self).setUp()
        self.url = reverse('mkt.developers.bango.payment_account',
                           args=[self.account.pk])

    def test_login_required(self):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.url))

    @mock.patch('mkt.developers.models.client')
    def test_get(self, client):
        client.call_uri.return_value = {'vendorName': 'testval'}

        res = self.client.get(self.url)
        client.call_uri.assert_called_with(self.account.uri)

        eq_(res.status_code, 200)
        output = json.loads(res.content)
        eq_(output['account_name'], self.account.name)
        assert 'vendorName' in output, (
            'Details from Bango not getting merged in: %s' % output)
        eq_(output['vendorName'], 'testval')



class TestPaymentAccountsForm(PaymentsBase):

    def setUp(self):
        super(TestPaymentAccountsForm, self).setUp()
        self.url = reverse('mkt.developers.bango.payment_accounts_form')

    def test_login_required(self):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.url))

    def test_mine(self):
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        eq_(res.context['bango_account_list_form']
               .fields['accounts'].choices.queryset.get(), self.account)


class TestPaymentDelete(PaymentsBase):

    def setUp(self):
        super(TestPaymentDelete, self).setUp()
        self.url = reverse('mkt.developers.bango.delete_payment_account',
                           args=[self.account.pk])


    def test_login_required(self):
        self.client.logout()
        self.assertLoginRequired(self.client.post(self.url, data={}))

    def test_not_mine(self):
        self.login(UserProfile.objects.get(pk=5497308))
        eq_(self.client.post(self.url, data={}).status_code, 404)

    def test_mine(self):
        eq_(self.client.post(self.url, data={}).status_code, 200)
        eq_(PaymentAccount.objects.get(pk=self.account.pk).inactive, True)

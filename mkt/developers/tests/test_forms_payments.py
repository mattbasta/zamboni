from django.conf import settings

import mock
from test_utils import RequestFactory

import amo
import amo.tests

from addons.models import Addon
from editors.models import RereviewQueue
from market.models import Price
from users.models import UserProfile

from mkt.developers import forms


class TestPaypalSetupForm(amo.tests.TestCase):

    def test_email_required(self):
        data = {'email': ''}
        assert not forms.PaypalSetupForm(data=data).is_valid()

    def test_email_gotten(self):
        data = {'email': 'foo@bar.com'}
        assert forms.PaypalSetupForm(data=data).is_valid()

    def test_email_malformed(self):
        data = {'email': 'foo'}
        assert not forms.PaypalSetupForm(data=data).is_valid()


class TestFreeToPremium(amo.tests.TestCase):
    fixtures = ['webapps/337141-steamcube']

    def setUp(self):
        self.request = RequestFactory()
        self.addon = Addon.objects.get(pk=337141)
        self.price = Price.objects.create(price='0.99')
        self.user = UserProfile.objects.get(email='steamcube@mozilla.com')

    def test_free_to_premium(self):
        kwargs = {
            'request': self.request,
            'extra': {
                'addon': self.addon,
                'amo_user': self.user,
                'dest': 'payment',
            }
        }
        data = {
            'premium_type': amo.ADDON_PREMIUM,
            'price': self.price.id,
        }
        form = forms.PremiumForm(data=data, **kwargs)
        assert form.is_valid()
        form.save()
        eq_(RereviewQueue.objects.count(), 1)

    def test_free_to_premium_pending(self):
        # Pending apps shouldn't get re-reviewed.
        self.addon.update(status=amo.STATUS_PENDING)

        kwargs = {
            'request': self.request,
            'extra': {
                'addon': self.addon,
                'amo_user': self.user,
                'dest': 'payment',
            }
        }
        data = {
            'premium_type': amo.ADDON_PREMIUM,
            'price': self.price.id,
        }
        form = forms.PremiumForm(data=data, **kwargs)
        assert form.is_valid()
        form.save()
        eq_(RereviewQueue.objects.count(), 0)

    def test_premium_to_free(self):
        # Premium to Free is ok for public apps.
        self.make_premium(self.addon)

        kwargs = {
            'request': self.request,
            'extra': {
                'addon': self.addon,
                'amo_user': self.user,
                'dest': 'payment',
            }
        }
        data = {'premium_type': amo.ADDON_FREE}
        form = forms.PremiumForm(data=data, **kwargs)
        assert form.is_valid()
        form.save()
        eq_(RereviewQueue.objects.count(), 0)


class TestInappConfigForm(amo.tests.TestCase):
    fixtures = ['webapps/337141-steamcube']

    def setUp(self):
        self.addon = Addon.objects.get(pk=337141)

    def submit(self, **params):
        data = {'postback_url': '/p',
                'chargeback_url': '/c',
                'is_https': False}
        data.update(params)
        fm = forms.InappConfigForm(data=data)
        cfg = fm.save(commit=False)
        cfg.addon = self.addon
        cfg.save()
        return cfg

    @mock.patch.object(settings, 'INAPP_REQUIRE_HTTPS', True)
    def test_cannot_override_https(self):
        cfg = self.submit(is_https=False)
        # This should be True because you cannot configure https.
        eq_(cfg.is_https, True)

    @mock.patch.object(settings, 'INAPP_REQUIRE_HTTPS', False)
    def test_can_override_https(self):
        cfg = self.submit(is_https=False)
        eq_(cfg.is_https, False)

from datetime import datetime, timedelta
import json
import unittest

from django.conf import settings

import mock
from nose import SkipTest
from nose.tools import eq_, raises
import waffle

from addons.models import (Addon, AddonCategory, AddonDeviceType, AddonPremium,
                           BlacklistedSlug, Category, Preview)
import amo
from amo.tests import TestCase
from constants.applications import DEVICE_TYPES
from market.models import Price
from mkt.developers.tests.test_views import BaseWebAppTest
from mkt.webapps.models import Webapp
from files.models import File
from versions.models import Version


class TestWebapp(TestCase):

    def test_hard_deleted(self):
        # Uncomment when redis gets fixed on ci.mozilla.org.
        raise SkipTest

        w = Webapp.objects.create(status=amo.STATUS_PUBLIC)
        # Until bug 755214 is fixed, `len` that ish.
        eq_(len(Webapp.objects.all()), 1)
        eq_(len(Webapp.with_deleted.all()), 1)

        w.delete('boom shakalakalaka')
        eq_(len(Webapp.objects.all()), 0)
        eq_(len(Webapp.with_deleted.all()), 0)

    def test_soft_deleted(self):
        # Uncomment when redis gets fixed on ci.mozilla.org.
        raise SkipTest

        waffle.models.Switch.objects.create(name='soft_delete', active=True)

        w = Webapp.objects.create(slug='ballin', app_slug='app-ballin',
                                  app_domain='http://omg.org/yes',
                                  status=amo.STATUS_PENDING)
        eq_(len(Webapp.objects.all()), 1)
        eq_(len(Webapp.with_deleted.all()), 1)

        w.delete('boom shakalakalaka')
        eq_(len(Webapp.objects.all()), 0)
        eq_(len(Webapp.with_deleted.all()), 1)

        # When an app is deleted its slugs and domain should get relinquished.
        post_mortem = Webapp.with_deleted.filter(id=w.id)
        eq_(post_mortem.count(), 1)
        for attr in ('slug', 'app_slug', 'app_domain'):
            eq_(getattr(post_mortem[0], attr), None)

    def test_soft_deleted_valid(self):
        w = Webapp.objects.create(status=amo.STATUS_PUBLIC)
        Webapp.objects.create(status=amo.STATUS_DELETED)
        eq_(list(Webapp.objects.valid()), [w])
        eq_(sorted(Webapp.with_deleted.valid()), [w])

    def test_webapp_type(self):
        webapp = Webapp()
        webapp.save()
        eq_(webapp.type, amo.ADDON_WEBAPP)

    def test_app_slugs_separate_from_addon_slugs(self):
        Addon.objects.create(type=1, slug='slug')
        webapp = Webapp(app_slug='slug')
        webapp.save()
        eq_(webapp.slug, 'app-%s' % webapp.id)
        eq_(webapp.app_slug, 'slug')

    def test_app_slug_collision(self):
        Webapp(app_slug='slug').save()
        w2 = Webapp(app_slug='slug')
        w2.save()
        eq_(w2.app_slug, 'slug-1')

        w3 = Webapp(app_slug='slug')
        w3.save()
        eq_(w3.app_slug, 'slug-2')

    def test_app_slug_blocklist(self):
        BlacklistedSlug.objects.create(name='slug')
        w = Webapp(app_slug='slug')
        w.save()
        eq_(w.app_slug, 'slug~')

    def test_get_url_path(self):
        self.skip_if_disabled(settings.REGION_STORES)
        webapp = Webapp(app_slug='woo')
        eq_(webapp.get_url_path(), '/app/woo/')

    def test_get_stats_url(self):
        self.skip_if_disabled(settings.REGION_STORES)
        webapp = Webapp(app_slug='woo')

        eq_(webapp.get_stats_url(), '/app/woo/statistics/')

        url = webapp.get_stats_url(action='installs_series',
            args=['day', '20120101', '20120201', 'json'])
        eq_(url, '/app/woo/statistics/installs-day-20120101-20120201.json')

    def test_get_inapp_stats_url(self):
        self.skip_if_disabled(settings.REGION_STORES)
        webapp = Webapp.objects.create(app_slug='woo')
        eq_(webapp.get_stats_inapp_url(action='revenue', inapp='duh'),
            '/app/woo/statistics/inapp/duh/sales/')

    def test_get_origin(self):
        url = 'http://www.xx.com:4000/randompath/manifest.webapp'
        webapp = Webapp(manifest_url=url)
        eq_(webapp.origin, 'http://www.xx.com:4000')

    def test_reviewed(self):
        assert not Webapp().is_unreviewed()

    def test_cannot_be_purchased(self):
        eq_(Webapp(premium_type=True).can_be_purchased(), False)
        eq_(Webapp(premium_type=False).can_be_purchased(), False)

    def test_can_be_purchased(self):
        w = Webapp(status=amo.STATUS_PUBLIC, premium_type=True)
        eq_(w.can_be_purchased(), True)

        w = Webapp(status=amo.STATUS_PUBLIC, premium_type=False)
        eq_(w.can_be_purchased(), False)

    def test_get_previews(self):
        w = Webapp.objects.create()
        eq_(w.get_promo(), None)

        p = Preview.objects.create(addon=w, position=0)
        eq_(list(w.get_previews()), [p])

        p.update(position=-1)
        eq_(list(w.get_previews()), [])

    def test_get_promo(self):
        w = Webapp.objects.create()
        eq_(w.get_promo(), None)

        p = Preview.objects.create(addon=w, position=0)
        eq_(w.get_promo(), None)

        p.update(position=-1)
        eq_(w.get_promo(), p)

    def test_mark_done_pending(self):
        w = Webapp()
        eq_(w.status, amo.STATUS_NULL)
        w.mark_done()
        eq_(w.status, amo.WEBAPPS_UNREVIEWED_STATUS)

    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_no_icon_in_manifest(self, get_manifest_json):
        webapp = Webapp()
        get_manifest_json.return_value = {}
        eq_(webapp.has_icon_in_manifest(), False)

    @mock.patch('mkt.webapps.models.Webapp.get_manifest_json')
    def test_has_icon_in_manifest(self, get_manifest_json):
        webapp = Webapp()
        get_manifest_json.return_value = {'icons': {}}
        eq_(webapp.has_icon_in_manifest(), True)

    def test_has_price(self):
        webapp = Webapp(premium_type=amo.ADDON_PREMIUM)
        webapp._premium = mock.Mock()
        eq_(webapp.has_price(), True)

    def test_has_no_price(self):
        webapp = Webapp(premium_type=amo.ADDON_PREMIUM)
        webapp._premium = mock.Mock()
        webapp._premium.price = None
        eq_(webapp.has_price(), False)

    def test_has_no_premium(self):
        webapp = Webapp(premium_type=amo.ADDON_PREMIUM)
        webapp._premium = None
        eq_(webapp.has_price(), False)

    def test_not_premium(self):
        eq_(Webapp().has_price(), False)


class TestWebappVersion(amo.tests.TestCase):
    fixtures = ['base/platforms']

    def test_no_version(self):
        eq_(Webapp().get_latest_file(), None)

    def test_no_file(self):
        webapp = Webapp.objects.create(manifest_url='http://foo.com')
        webapp._current_version = Version.objects.create(addon=webapp)
        eq_(webapp.get_latest_file(), None)

    def test_right_file(self):
        webapp = Webapp.objects.create(manifest_url='http://foo.com')
        version = Version.objects.create(addon=webapp)
        old_file = File.objects.create(version=version, platform_id=1)
        old_file.update(created=datetime.now() - timedelta(days=1))
        new_file = File.objects.create(version=version, platform_id=1)
        webapp._current_version = version
        eq_(webapp.get_latest_file().pk, new_file.pk)


class TestWebappManager(TestCase):

    def setUp(self):
        self.reviewed_eq = (lambda f=[]:
                            eq_(list(Webapp.objects.reviewed()), f))
        self.listed_eq = (lambda f=[]: eq_(list(Webapp.objects.visible()), f))

    def test_reviewed(self):
        for status in amo.REVIEWED_STATUSES:
            w = Webapp.objects.create(status=status)
            self.reviewed_eq([w])
            Webapp.objects.all().delete()

    def test_unreviewed(self):
        for status in amo.UNREVIEWED_STATUSES:
            Webapp.objects.create(status=status)
            self.reviewed_eq()
            Webapp.objects.all().delete()

    def test_listed(self):
        # Public status, non-null current version, non-user-disabled.
        w = Webapp.objects.create(status=amo.STATUS_PUBLIC)
        w._current_version = Version.objects.create(addon=w)
        w.save()
        self.listed_eq([w])

    def test_unlisted(self):
        # Public, null current version, non-user-disabled.
        w = Webapp.objects.create()
        self.listed_eq()

        # With current version but unreviewed.
        Version.objects.create(addon=w)
        self.listed_eq()

        # And user-disabled.
        w.update(disabled_by_user=True)
        self.listed_eq()


class TestManifest(BaseWebAppTest):

    def test_get_manifest_json(self):
        webapp = self.post_addon()
        assert webapp.current_version
        assert webapp.current_version.has_files
        with open(self.manifest, 'r') as mf:
            manifest_json = json.load(mf)
            eq_(webapp.get_manifest_json(), manifest_json)


class TestDomainFromURL(unittest.TestCase):

    def test_simple(self):
        eq_(Webapp.domain_from_url('http://mozilla.com/'),
            'http://mozilla.com')

    def test_long_path(self):
        eq_(Webapp.domain_from_url('http://mozilla.com/super/rad.webapp'),
            'http://mozilla.com')

    def test_no_normalize_www(self):
        eq_(Webapp.domain_from_url('http://www.mozilla.com/super/rad.webapp'),
            'http://www.mozilla.com')

    def test_with_port(self):
        eq_(Webapp.domain_from_url('http://mozilla.com:9000/'),
            'http://mozilla.com:9000')

    def test_subdomains(self):
        eq_(Webapp.domain_from_url('http://apps.mozilla.com/'),
            'http://apps.mozilla.com')

    def test_https(self):
        eq_(Webapp.domain_from_url('https://mozilla.com/'),
            'https://mozilla.com')

    def test_normalize_case(self):
        eq_(Webapp.domain_from_url('httP://mOzIllA.com/'),
            'http://mozilla.com')

    @raises(ValueError)
    def test_none(self):
        Webapp.domain_from_url(None)

    @raises(ValueError)
    def test_empty(self):
        Webapp.domain_from_url('')


class TestTransformer(amo.tests.TestCase):
    fixtures = ['webapps/337141-steamcube']

    def setUp(self):
        self.device = DEVICE_TYPES.keys()[0]

    @mock.patch('mkt.webapps.models.Addon.transformer')
    def test_addon_transformer_called(self, transformer):
        transformer.return_value = {}
        list(Webapp.objects.all())
        assert transformer.called

    def test_device_types(self):
        AddonDeviceType.objects.create(addon_id=337141, device_type=self.device)
        webapps = list(Webapp.objects.filter(id=337141))

        with self.assertNumQueries(0):
            for webapp in webapps:
                assert webapp._device_types
                eq_(webapp.device_types, [DEVICE_TYPES[self.device]])

    def test_device_type_cache(self):
        webapp = Webapp.objects.get(id=337141)
        webapp._device_types = []
        with self.assertNumQueries(0):
            eq_(webapp.device_types, [])


class TestIsComplete(amo.tests.TestCase):

    def setUp(self):
        self.device = DEVICE_TYPES.keys()[0]
        self.cat = Category.objects.create(name='c', type=amo.ADDON_WEBAPP)
        self.webapp = Webapp.objects.create(type=amo.ADDON_WEBAPP,
                                            status=amo.STATUS_NULL)

    def fail(self, value):
        can, reasons = self.webapp.is_complete()
        eq_(can, False)
        assert value in reasons[0], reasons

    def test_fail(self):
        self.fail('email')

        self.webapp.support_email = 'a@a.com'
        self.webapp.save()
        self.fail('name')

        self.webapp.name = 'name'
        self.webapp.save()
        self.fail('device')

        self.webapp.addondevicetype_set.create(device_type=self.device)
        self.webapp.save()
        self.fail('category')

        AddonCategory.objects.create(addon=self.webapp, category=self.cat)
        self.fail('screenshot')

        self.webapp.previews.create()
        eq_(self.webapp.is_complete()[0], True)

    def test_paypal(self):
        self.webapp.update(premium_type=amo.ADDON_PREMIUM)
        self.fail('payments')

        self.webapp.update(paypal_id='a@a.com')
        self.fail('price')

    def test_no_price(self):
        self.webapp.update(premium_type=amo.ADDON_PREMIUM, paypal_id='a@a.com')
        AddonPremium.objects.create(addon=self.webapp, currencies=['BRL'])
        self.fail('price')

    def test_price(self):
        self.webapp.update(premium_type=amo.ADDON_PREMIUM, paypal_id='a@a.com')
        AddonPremium.objects.create(addon=self.webapp, currencies=['BRL'],
                                    price=Price.objects.create(price='1'))
        self.fail('email')

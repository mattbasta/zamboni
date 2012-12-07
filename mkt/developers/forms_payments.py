from django import forms
from django.conf import settings

import commonware
import happyforms
import waffle
from tower import ugettext as _, ugettext_lazy as _lazy

import amo
from amo.utils import raise_required
import paypal
from addons.models import Addon, AddonUpsell
from lib.pay_server import client
from market.models import AddonPremium, Price, PriceCurrency

from mkt.constants import FREE_PLATFORMS, PAID_PLATFORMS
from mkt.inapp_pay.models import InappConfig
from mkt.site.forms import AddonChoiceField

from .models import AddonBangoPaymentAccount, BangoPaymentAccount


log = commonware.log.getLogger('z.devhub')


class PremiumForm(happyforms.Form):
    """
    The premium details for an addon, which is unfortunately
    distributed across a few models.
    """

    allow_inapp = forms.BooleanField(
        label=_lazy(u'Allow In-App Purchases?'), required=False)
    price = forms.ModelChoiceField(queryset=Price.objects.active(),
                                   label=_lazy(u'App Price'),
                                   empty_label=None, required=False)
    currencies = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple,
        required=False, label=_lazy(u'Supported Non-USD Currencies'))

    free_platforms = forms.MultipleChoiceField(
        choices=FREE_PLATFORMS, required=False)
    paid_platforms = forms.MultipleChoiceField(
        choices=PAID_PLATFORMS, required=False)

    def __init__(self, *args, **kw):
        self.request = kw.pop('request')
        self.addon = kw.pop('addon')
        self.user = kw.pop('user')

        kw['initial'] = {
            'allow_inapp': self.addon.premium_type in amo.ADDON_INAPPS
        }
        if self.addon.premium:
            # If the app has a premium object, set the initial price.
            kw['initial']['price'] = self.addon.premium.price

        super(PremiumForm, self).__init__(*args, **kw)

        if self.addon.premium_type in amo.ADDON_PREMIUMS:
            # Require the price field if the app is premium.
            self.fields['price'].required = True

        # Get the list of supported devices and put them in the data.
        self.device_data = {}
        supported_devices = [amo.REVERSE_DEVICE_LOOKUP[dev.id] for dev in
                             self.addon.device_types]

        for platform in [x[0].split('-')[1] for x in
                         FREE_PLATFORMS + PAID_PLATFORMS]:
            supported = platform in supported_devices
            self.device_data['free-%s' % platform] = supported
            self.device_data['paid-%s' % platform] = supported

        choices = (PriceCurrency.objects.values_list('currency', flat=True)
                                        .distinct())
        self.fields['currencies'].choices = [(k, k) for k in choices if k]

        if (not self.initial.get('price') and
            len(self.fields['price'].choices) > 1):
            # Tier 0 (Free) should not be the default selection.
            self.initial['price'] = self._initial_price()

    def _initial_price(self):
        return Price.objects.active().exclude(price='0.00')[0]

    def clean_price(self):
        if (self.cleaned_data.get('premium_type') in amo.ADDON_PREMIUMS
            and not self.cleaned_data['price']):

            raise_required()

        return self.cleaned_data['price']

    def is_toggling(self):
        return self.request.POST.get('toggle-paid') or False

    def save(self):
        toggle = self.is_toggling()
        upsell = self.addon.upsold
        is_premium = self.addon.premium_type in amo.ADDON_PREMIUMS

        if toggle == 'paid' and self.addon.premium_type == amo.ADDON_FREE:
            # Toggle free apps to paid by giving them a premium object.
            premium = self.addon.premium
            if not premium:
                premium = AddonPremium()
                premium.addon = self.addon
            premium.price = self._initial_price()
            premium.save()

            self.addon.premium_type = amo.ADDON_PREMIUM
            self.addon.status = amo.STATUS_NULL

        elif toggle == 'free' and is_premium:
            # If the app is paid and we're making it free, remove it as an
            # upsell (if an upsell exists).
            upsell = self.addon.upsold
            if upsell:
                upsell.delete()

            self.addon.premium_type = amo.ADDON_FREE

            if self.addon.status == amo.STATUS_NULL:
                # If the app was marked as incomplete because it didn't have a
                # payment account, mark it as either its highest status, or as
                # PENDING if it was never reviewed (highest_status == NULL).
                self.addon.status = (
                    self.addon.highest_status if
                    self.addon.highest_status != amo.STATUS_NULL else
                    amo.STATUS_PENDING)

        elif is_premium:
            # The dev is submitting updates for payment data about a paid app.
            self.addon.premium_type = (
                amo.ADDON_PREMIUM_INAPP if
                self.cleaned_data.get('allow_inapp') else amo.ADDON_PREMIUM)

            if 'price' in self.cleaned_data:
                self.addon.premium.update(price=self.cleaned_data['price'])

            if 'currencies' in self.cleaned_data:
                self.addon.premium.update(
                    currencies=self.cleaned_data['currencies'])

        self.addon.save()


class UpsellForm(happyforms.Form):

    upsell_of = AddonChoiceField(queryset=Addon.objects.none(), required=False,
                                 label=_lazy(u'This is a paid upgrade of'),
                                 empty_label=_lazy(u'Not an upgrade'))

    def __init__(self, *args, **kw):
        self.addon = kw.pop('addon')
        self.user = kw.pop('user')

        kw.setdefault('initial', {})
        if self.addon.upsold:
            kw['initial']['upsell_of'] = self.addon.upsold.free

        super(UpsellForm, self).__init__(*args, **kw)

        self.fields['upsell_of'].queryset = (
            self.user.addons.exclude(pk=self.addon.pk)
                            .filter(premium_type__in=amo.ADDON_FREES,
                                    type=self.addon.type))

    def save(self):
        current_upsell = self.addon.upsold
        new_upsell_app = self.cleaned_data['upsell_of']

        if new_upsell_app:
            # We're changing the upsell or creating a new one.

            if not current_upsell:
                # If the upsell is new or we just deleted the old upsell,
                # create a new upsell.
                current_upsell = AddonUpsell(premium=self.addon)

            # Set the upsell object to point to the app that we're upselling.
            current_upsell.free = new_upsell_app
            current_upsell.save()

        elif not new_upsell_app and current_upsell:
            # We're deleting the upsell.
            current_upsell.delete()


class BangoPaymentAccounts(happyforms.Form):

    accounts = forms.ModelChoiceField(
        queryset=BangoPaymentAccount.objects.none(),
        label=_lazy(u'Payment Account'), required=False)

    def __init__(self, *args, **kw):
        self.request = kw.pop('request')
        self.addon = kw.pop('addon')

        kw.setdefault('initial', {})
        if self.addonbangopaymentaccount:
            kw['initial']['upsell_of'] = self.addon.upsold.free

        super(BangoPaymentAccounts, self).__init__(*args, **kw)


class InappConfigForm(happyforms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(InappConfigForm, self).__init__(*args, **kwargs)
        if settings.INAPP_REQUIRE_HTTPS:
            self.fields['is_https'].widget.attrs['disabled'] = 'disabled'
            self.initial['is_https'] = True

    def clean_is_https(self):
        if settings.INAPP_REQUIRE_HTTPS:
            return True  # cannot override it with form values
        else:
            return self.cleaned_data['is_https']

    def clean_postback_url(self):
        return self._clean_relative_url(self.cleaned_data['postback_url'])

    def clean_chargeback_url(self):
        return self._clean_relative_url(self.cleaned_data['chargeback_url'])

    def _clean_relative_url(self, url):
        url = url.strip()
        if not url.startswith('/'):
            raise forms.ValidationError(
                _('This URL is relative to your app domain so it must start '
                  'with a slash.'))
        return url

    class Meta:
        model = InappConfig
        fields = ('postback_url', 'chargeback_url', 'is_https')


# TODO: Figure out either a.) where to pull these from and implement that
# or b.) which constants file to move it to.
# TODO: Add more of these?
COUNTRIES = ['BRA', 'ESP']

class BangoPaymentAccountForm(happyforms.Form):

    bankAccountPayeeName = forms.CharField(
        max_length=50, label=_lazy(u'Account Holder Name'))
    companyName = forms.CharField(max_length=255, label=_lazy(u'Company Name'))
    vendorName = forms.CharField(
        max_length=255, label=_lazy(u'Vendor Name'))
    financeEmailAddress = forms.EmailField(
        required=False, label=_lazy(u'Financial Email'))
    adminEmailAddress = forms.EmailField(
        required=False, label=_lazy(u'Administrative Email'))

    address1 = forms.CharField(
        max_length=255, label=_lazy(u'Address'))
    address2 = forms.CharField(
        max_length=255, required=False, label=_lazy(u'Address 2'))
    addressCity = forms.CharField(
        max_length=128, label=_lazy(u'City/Municipality'))
    addressState = forms.CharField(
        max_length=64, label=_lazy(u'State/Province/Region'))
    addressZipCode = forms.CharField(
        max_length=128, label=_lazy(u'Zip/Postal Code'))
    addressPhone = forms.CharField(max_length=20, label=_lazy(u'Phone'))
    countryIso = forms.ChoiceField(label=_lazy(u'Country'))
    currencyIso = forms.ChoiceField(label=_lazy(u'I prefer to be paid in'))

    vatNumber = forms.CharField(
        max_length=17, required=False, label=_lazy(u'VAT Number'))

    bankAccountNumber = forms.CharField(
        max_length=20, required=False, label=_lazy(u'Bank Account Number'))
    bankAccountCode = forms.CharField(
        max_length=20, label=_lazy(u'Bank Account Code'))
    bankName = forms.CharField(max_length=50, label=_lazy(u'Bank Name'))
    bankAddress1 = forms.CharField(max_length=50, label=_lazy(u'Bank Address'))
    bankAddress2 = forms.CharField(
        max_length=50, required=False, label=_lazy(u'Bank Address 2'))
    bankAddressCity = forms.CharField(max_length=50, required=False,
                                      label=_lazy(u'Bank City/Municipality'))
    bankAddressState = forms.CharField(
        max_length=50, required=False,
        label=_lazy(u'Bank State/Province/Region'))
    bankAddressZipCode = forms.CharField(max_length=50,
                                         label=_lazy(u'Bank Zip/Postal Code'))
    bankAddressIso = forms.ChoiceField(label=_lazy(u'Bank Country'))

    account_name = forms.CharField(max_length=64, label=_(u'Account Name'))

    def __init__(self, *args, **kwargs):
        super(BangoPaymentAccountForm, self).__init__(*args, **kwargs)

        currency_choices = (
            PriceCurrency.objects.values_list('currency', flat=True)
                                 .distinct())
        self.fields['currencyIso'].choices = [('USD', 'USD')] + [
            (k, k) for k in filter(None, currency_choices)]

        country_choices = [(k, k) for k in COUNTRIES]
        self.fields['bankAddressIso'].choices = country_choices
        self.fields['countryIso'].choices = country_choices


class BangoAccountListForm(happyforms.Form):
    accounts = forms.ModelChoiceField(
        queryset=BangoPaymentAccount.objects.none(),
        label=_lazy(u'Payment Account'), required=False)

    def __init__(self, user, addon=None, *args, **kwargs):
        self.addon = addon

        super(BangoAccountListForm, self).__init__(*args, **kwargs)

        self.fields['accounts'].queryset = (
            BangoPaymentAccount.objects.filter(user=user, inactive=False))

        try:
            current_account = AddonBangoPaymentAccount.objects.get(addon=addon)
            self.initial['accounts'] = current_account.bango_account
            self.fields['accounts'].empty_label = None
        except AddonBangoPaymentAccount.DoesNotExist:
            pass

    def clean_accounts(self):
        if (AddonBangoPaymentAccount.objects.filter(addon=self.addon)
                                            .exists() and
                not self.cleaned_data.get('accounts')):
            raise forms.ValidationError(
                _('You cannot remove a payment account from an app.'))

    def save(self):
        if self.cleaned_data.get('accounts'):
            try:
                AddonBangoPaymentAccount.objects.get(addon=self.addon).update(
                    bango_account=self.cleaned_data['accounts'])
            except AddonBangoPaymentAccount.DoesNotExist:
                addon_account = AddonBangoPaymentAccount.objects.create(
                    addon=self.addon,
                    bango_account=self.cleaned_data['accounts'])

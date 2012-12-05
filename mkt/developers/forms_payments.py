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
from editors.models import RereviewQueue
from market.models import AddonPremium, Price, PriceCurrency

from mkt.constants import FREE_PLATFORMS, PAID_PLATFORMS
from mkt.inapp_pay.models import InappConfig
from mkt.site.forms import AddonChoiceField

log = commonware.log.getLogger('z.devhub')
paypal_log = commonware.log.getLogger('mkt.paypal')


class PremiumForm(happyforms.Form):
    """
    The premium details for an addon, which is unfortunately
    distributed across a few models.
    """

    #premium_type = forms.TypedChoiceField(label=_lazy(u'Premium Type'),
    #    coerce=lambda x: int(x), choices=amo.ADDON_PREMIUM_TYPES.items(),
    #    widget=forms.RadioSelect())
    #price = forms.ModelChoiceField(queryset=Price.objects.active(),
    #                               label=_lazy(u'App Price'),
    #                               empty_label=None,
    #                               required=False)
    #free = AddonChoiceField(queryset=Addon.objects.none(), required=False,
    #                        label=_lazy(u'This is a paid upgrade of'),
    #                        empty_label=_lazy(u'Not an upgrade'))
    #currencies = forms.MultipleChoiceField(
    #    widget=forms.CheckboxSelectMultiple,
    #    required=False, label=_lazy(u'Supported Non-USD Currencies'))

    free_platforms = forms.MultipleChoiceField(
        choices=FREE_PLATFORMS, required=False)
    paid_platforms = forms.MultipleChoiceField(
        choices=PAID_PLATFORMS, required=False)

    REVERSE_DEVICE_LOOKUP = {
        amo.DEVICE_GAIA.id: 'os',
        amo.DEVICE_DESKTOP.id: 'desktop',
        amo.DEVICE_MOBILE.id: 'phone',
        amo.DEVICE_TABLET.id: 'tablet',
    }

    def __init__(self, *args, **kw):
        self.extra = kw.pop('extra')
        self.request = kw.pop('request')
        self.addon = self.extra['addon']

        kw['initial'] = {'premium_type': self.addon.premium_type}
        if self.addon.premium:
            kw['initial']['price'] = self.addon.premium.price

        super(PremiumForm, self).__init__(*args, **kw)

        # Get the list of supported devices and put them in the data.
        self.device_data = {}
        supported_devices = [self.REVERSE_DEVICE_LOOKUP[dev.id] for dev in
                             self.addon.device_types]
        for platform in [x[0].split('-')[1] for x in
                         FREE_PLATFORMS + PAID_PLATFORMS]:
            supported = platform in supported_devices
            self.device_data["free-%s" % platform] = supported
            self.device_data["paid-%s" % platform] = supported

        #if waffle.switch_is_active('currencies'):
        #    choices = (PriceCurrency.objects.values_list('currency', flat=True)
        #               .distinct())
        #    self.fields['currencies'].choices = [(k, k)
        #                                         for k in choices if k]

        self.fields['free'].queryset = (self.extra['amo_user'].addons
            .exclude(pk=self.addon.pk)
            .filter(premium_type__in=amo.ADDON_FREES,
                    status__in=amo.VALID_STATUSES,
                    type=self.addon.type))
        if (not self.initial.get('price') and
            len(list(self.fields['price'].choices)) > 1):
            # Tier 0 (Free) should not be the default selection.
            self.initial['price'] = (Price.objects.active()
                                     .exclude(price='0.00')[0])

        # For the wizard, we need to remove some fields.
        for field in self.extra.get('exclude', []):
            del self.fields[field]

    def clean_price(self):
        if (self.cleaned_data.get('premium_type') in amo.ADDON_PREMIUMS
            and not self.cleaned_data['price']):
            raise_required()
        return self.cleaned_data['price']

    def clean_free(self):
        return self.cleaned_data['free']

    def save(self):
        if self.request.POST
        if 'price' in self.cleaned_data:
            premium = self.addon.premium
            if not premium:
                premium = AddonPremium()
                premium.addon = self.addon
            premium.price = self.cleaned_data['price']
            premium.save()

        upsell = self.addon.upsold
        if self.cleaned_data['free']:

            # Check if this app was already a premium version for another app.
            if upsell and upsell.free != self.cleaned_data['free']:
                upsell.delete()

            if not upsell:
                upsell = AddonUpsell(premium=self.addon)
            upsell.free = self.cleaned_data['free']
            upsell.save()
        elif not self.cleaned_data['free'] and upsell:
            upsell.delete()

        # Check for free -> paid for already public apps.
        premium_type = self.cleaned_data['premium_type']
        if (self.addon.premium_type == amo.ADDON_FREE and
            premium_type in amo.ADDON_PREMIUMS and
            self.addon.status == amo.STATUS_PUBLIC):
            # Free -> paid for public apps trigger re-review.
            log.info(u'[Webapp:%s] (Re-review) Public app, free -> paid.' % (
                self.addon))
            RereviewQueue.flag(self.addon, amo.LOG.REREVIEW_FREE_TO_PAID)

        self.addon.premium_type = premium_type

        if self.addon.premium and waffle.switch_is_active('currencies'):
            currencies = self.cleaned_data['currencies']
            self.addon.premium.update(currencies=currencies)

        self.addon.save()

        # If they checked later in the wizard and then decided they want
        # to keep it free, push to pending.
        if not self.addon.needs_paypal() and self.addon.is_incomplete():
            self.addon.mark_done()


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
            raise forms.ValidationError(_('This URL is relative to your app '
                                          'domain so it must start with a '
                                          'slash.'))
        return url

    class Meta:
        model = InappConfig
        fields = ('postback_url', 'chargeback_url', 'is_https')


class PaypalSetupForm(happyforms.Form):
    email = forms.EmailField(required=False,
                             label=_lazy(u'PayPal email address'))

    def clean(self):
        data = self.cleaned_data
        if not data.get('email'):
            msg = _(u'The PayPal email is required.')
            self._errors['email'] = self.error_class([msg])

        return data


class PaypalPaymentData(happyforms.Form):
    first_name = forms.CharField(max_length=255, required=False)
    last_name = forms.CharField(max_length=255, required=False)
    full_name = forms.CharField(max_length=255, required=False)
    business_name = forms.CharField(max_length=255, required=False)
    country = forms.CharField(max_length=64)
    address_one = forms.CharField(max_length=255)
    address_two = forms.CharField(max_length=255,  required=False)
    post_code = forms.CharField(max_length=128, required=False)
    city = forms.CharField(max_length=128, required=False)
    state = forms.CharField(max_length=64, required=False)
    phone = forms.CharField(max_length=32, required=False)


def check_paypal_id(paypal_id):
    if not paypal_id:
        raise forms.ValidationError(
            _('PayPal ID required to accept contributions.'))
    try:
        valid, msg = paypal.check_paypal_id(paypal_id)
        if not valid:
            raise forms.ValidationError(msg)
    except socket.error:
        raise forms.ValidationError(_('Could not validate PayPal id.'))


class BankDetailsForm(happyforms.Form):
    holder_name = forms.CharField(max_length=255, required=True)
    account_number = forms.CharField(max_length=40, required=True)
    preferred_currency = forms.MultipleChoiceField(
        choices=PriceCurrency.objects.values_list('currency', flat=True)
                                     .distinct(), required=True)

    vat_number = forms.CharField(max_length=17, required=False)

    address_one = forms.CharField(max_length=255,
                                  label=_lazy(u'Business Address'))
    address_two = forms.CharField(max_length=255,  required=False,
                                  label=_lazy(u'Business Address 2'))
    city = forms.CharField(max_length=128, required=False,
                           label=_lazy(u'City/Municipality'))
    state = forms.CharField(max_length=64, required=False,
                            label=_lazy(u'State/Province/Region'))
    post_code = forms.CharField(max_length=128, required=False,
                                label=_lazy(u'Zip/Postal Code'))
    country = forms.CharField(max_length=64, label=_lazy(u'Country'))

    business_name = forms.CharField(max_length=255, required=False,
                                    label=_lazy(u'Company name'))
    vendor_name = forms.CharField(max_length=255, required=False,
                                  label=_lazy(u'Vendor name'))

    financial_email = forms.EmailField(
        required=False, label=_lazy(u'Financial email'))
    administrative_email = forms.EmailField(
        required=False, label=_lazy(u'Administrative email'))

    def __init__(self, package, act_type='bango', *args, **kwargs):
        self.package = package
        super(BankDetailsForm, self).__init__(*args, **kwargs)

    def clean(self):
        data = self.cleaned_data
        return {
            'seller_bango': self.package,  # Bango package URL
            'bankAccountPayeeName': 'Andy',
            'bankAccountNumber': 'Yes',
            'bankAccountCode': '123',
            'bankName': 'Bailouts r us',
            'bankAddress1': '123 Yonge St',
            'bankAddressZipCode': 'V1V 1V1',
            'bankAddressIso': 'BRA'
        }

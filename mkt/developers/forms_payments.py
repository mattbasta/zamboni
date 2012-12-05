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


PREMIUM_STATUSES = [
    amo.ADDON_FREE,
    amo.ADDON_PREMIUM
]
PREMIUM_CHOICES = dict((k, v) for k, v in amo.ADDON_PREMIUM_TYPES.items() if
                       k in PREMIUM_STATUSES)
# A mapping of (PREMIUM_TYPE, <Allow in-app payments>)
PREMIUM_MAPPING = {
    (amo.ADDON_FREE, False): amo.ADDON_FREE,
    (amo.ADDON_FREE, True): amo.ADDON_FREE_INAPP,
    (amo.ADDON_PREMIUM, False): amo.ADDON_PREMIUM,
    (amo.ADDON_PREMIUM, True): amo.ADDON_PREMIUM_INAPP,
}

PREMIUM_REVERSE_MAPPING = {
    amo.ADDON_FREE: amo.ADDON_FREE,
    amo.ADDON_PREMIUM: amo.ADDON_PREMIUM,
    amo.ADDON_PREMIUM_INAPP: amo.ADDON_PREMIUM,
    amo.ADDON_FREE_INAPP: amo.ADDON_FREE,
    amo.ADDON_OTHER_INAPP: amo.ADDON_FREE
}


class PremiumForm(happyforms.Form):
    """
    The premium details for an addon, which is unfortunately
    distributed across a few models.
    """

    premium_type = forms.TypedChoiceField(
        label=_lazy(u'Premium Type'), widget=forms.Select(), required=False,
        coerce=lambda x: int(x), choices=PREMIUM_CHOICES.items())
    allow_inapp = forms.BooleanField(
        label=_lazy(u'Allow In-App Purchases?'), required=False)
    #price = forms.ModelChoiceField(queryset=Price.objects.active(),
    #                               label=_lazy(u'App Price'),
    #                               empty_label=None, required=False)
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

        kw['initial'] = {
            'premium_type': PREMIUM_REVERSE_MAPPING[self.addon.premium_type],
            'allow_inapp': self.addon.premium_type in amo.ADDON_INAPPS
        }
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

        #if (not self.initial.get('price') and
        #    len(list(self.fields['price'].choices)) > 1):
        #    # Tier 0 (Free) should not be the default selection.
        #    self.initial['price'] = (Price.objects.active()
        #                             .exclude(price='0.00')[0])

        # For the wizard, we need to remove some fields.
        for field in self.extra.get('exclude', []):
            if field in self.fields:
                del self.fields[field]

    def clean_price(self):
        if (self.cleaned_data.get('premium_type') in amo.ADDON_PREMIUMS
            and not self.cleaned_data['price']):
            raise_required()
        return self.cleaned_data['price']

    def save(self):
        toggle = self.request.POST.get('toggle-paid')
        upsell = self.addon.upsold

        if toggle == 'paid' and self.addon.premium_type == amo.ADDON_FREE:
            # Toggle free apps to paid by giving them a premium object.
            premium = self.addon.premium
            if not premium:
                premium = AddonPremium()
                premium.addon = self.addon
            premium.price = Price.objects.get(price='0.00')
            premium.save()

            self.addon.premium_type = amo.ADDON_PREMIUM

            # Free -> Paid for public apps brings a re-review.
            if self.addon.status == amo.STATUS_PUBLIC:
                log.info(u'[Webapp:%s] (Re-review) Public app, free -> paid.' %
                             self.addon)

                RereviewQueue.flag(self.addon, amo.LOG.REREVIEW_FREE_TO_PAID)

        elif (toggle == 'free' and
              self.addon.premium_type in amo.ADDON_PREMIUMS):
            # If the app is paid and we're making it free, remove it as an
            # upsell (if an upsell exists).
            upsell = self.addon.upsold
            if upsell:
                upsell.delete()

            self.addon.premium_type = amo.ADDON_FREE

        elif self.addon.premium_type in amo.ADDON_PREMIUMS:
            # The dev is submitting updates for payment data about a paid app.

            premium_type = self.cleaned_data.get('premium_type')
            allow_inapp = self.cleaned_data.get('allow_inapp')
            self.addon.premium_type = PREMIUM_MAPPING[premium_type, allow_inapp]

        #if self.addon.premium and waffle.switch_is_active('currencies'):
        #    currencies = self.cleaned_data['currencies']
        #    self.addon.premium.update(currencies=currencies)

        self.addon.save()

        # If they checked later in the wizard and then decided they want
        # to keep it free, push to pending.
        if not self.addon.needs_paypal() and self.addon.is_incomplete():
            self.addon.mark_done()


class UpsellForm(happyforms.Form):

    upsell_of = AddonChoiceField(queryset=Addon.objects.none(), required=False,
                                 label=_lazy(u'This is a paid upgrade of'),
                                 empty_label=_lazy(u'Not an upgrade'))

    def __init__(self, *args, **kw):
        self.request = kw.pop('request')
        self.addon = kw.pop('addon')

        super(UpsellForm, self).__init__(*args, **kw)

        self.fields['upsell_of'].queryset = (self.request.amo_user.addons
            .exclude(pk=self.addon.pk)
            .filter(premium_type__in=amo.ADDON_FREES,
                    status__in=amo.VALID_STATUSES,
                    type=self.addon.type))

    def clean_upsell_of(self):
        return self.cleaned_data['upsell_of']

    def save(self):
        current_upsell = self.addon.upsold
        new_upsell_app = self.cleaned_data['upsell_of']

        if new_upsell_app:
            # We're changing the upsell or creating a new one.

            if current_upsell and current_upsell.free != new_upsell_app:
                # The upsell is changing.
                current_upsell.delete()

            if not current_upsell:
                # If the upsell is new or we just deleted the old upsell,
                # create a new upsell.
                current_upsell = AddonUpsell(premium=self.addon)

            # Set the upsell object to point to the app that we're upselling.
            current_upsell.free = new_upsell_app
            current_upsell.save()

        if not new_upsell_app and current_upsell:
            # We're deleting the upsell.
            current_upsell.delete()


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
        self.act_type = act_type
        super(BankDetailsForm, self).__init__(*args, **kwargs)

    def clean(self):
        data = self.cleaned_data
        return {
            'seller_%s' % self.act_type: self.package,  # Seller package info.
            'bankAccountPayeeName': data['holder_name'],
            'bankAccountNumber': data['account_number'],
            'bankAccountCode': data[''],
            'bankName': 'Bailouts r us',
            'bankAddress1': '123 Yonge St',
            'bankAddressZipCode': 'V1V 1V1',
            'bankAddressIso': 'BRA'
        }

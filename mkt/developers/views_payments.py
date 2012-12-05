from django.shortcuts import redirect

import jingo
from tower import ugettext as _

import amo
from amo import messages
from amo.decorators import json_view, post_required, write
from lib.pay_server import client

from mkt.constants import DEVICE_LOOKUP
from mkt.developers.decorators import dev_required
from mkt.submit.forms import NewWebappForm

from . import forms

@dev_required(owner_for_post=True, webapp=True)
def payments(request, addon_id, addon, webapp=False):

    premium_form = forms.PremiumForm(
        request.POST or None, request=request,
        extra={'addon': addon, 'amo_user': request.amo_user,
               'dest': 'payment'})
    upsell_form = forms.UpsellForm(
        request.POST or None, request=request, addon=addon)

    if request.method == 'POST' and premium_form.is_valid():
        premium_form.save()
        messages.success(request, _('Changes successfully saved.'))
        return redirect(addon.get_dev_url('payments'))

    # TODO: This needs to be updated as more platforms support payments.
    cannot_be_paid = (
        addon.premium_type == amo.ADDON_FREE and
        any(premium_form.device_data['free-%s' % x] == y for x, y in
            [('phone', True), ('tablet', True), ('desktop', True),
             ('os', False)]))

    return jingo.render(
        request, 'developers/payments/premium.html',
        {'addon': addon, 'webapp': webapp, 'premium': addon.premium,
         'form': premium_form, 'upsell_form': upsell_form,
         'DEVICE_LOOKUP': DEVICE_LOOKUP,
         'is_paid': addon.premium_type == amo.ADDON_PREMIUM,
         'no_paid': cannot_be_paid})


# TODO: These should probably move to their own app.
@json_view
def payments_accounts(request):
    return http.HttpResponse('')


@write
@json_view
@post_required
def payments_accounts_add(request):
    return {'success': True}


@json_view
def payments_accounts_account(request, id):
    output = {}
    if request.method == 'POST':
        account_form = forms.BankDetailsForm(request.POST)
        if not account_form.is_valid():
            pass

    return {

    }

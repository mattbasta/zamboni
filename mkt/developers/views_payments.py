from django import http
from django.shortcuts import get_object_or_404, redirect

import commonware
import jingo
from tower import ugettext as _

import amo
from amo import messages
from amo.decorators import json_view, post_required, write
from editors.models import RereviewQueue
from lib.pay_server import client

from mkt.constants import DEVICE_LOOKUP
from mkt.developers.decorators import dev_required

from . import forms, models


log = commonware.log.getLogger('z.devhub')


@dev_required(owner_for_post=True, webapp=True)
def payments(request, addon_id, addon, webapp=False):

    premium_form = forms.PremiumForm(
        request.POST or None, request=request, addon=addon,
        user=request.amo_user)

    upsell_form = forms.UpsellForm(
        request.POST or None, addon=addon, user=request.amo_user)

    bango_account_list_form = forms.BangoAccountListForm(
        request.amo_user, addon, request.POST or None)

    if request.method == 'POST':
        success = all(form.is_valid() for form in
                      [premium_form, upsell_form, bango_account_list_form])

        if success:
            toggling = premium_form.is_toggling()

            try:
                premium_form.save()
            except client.Error as e:
                success = False
                messages.error(
                    request, _(u'We encountered a problem connecting to the '
                               u'payment server.'))

            is_now_paid = addon.premium_type in amo.ADDON_PREMIUMS

            # If we haven't changed to a free app, check the upsell.
            if is_now_paid and success:
                upsell_form.save()
                bango_account_list_form.save()

            # If the app is marked as paid and the information is complete
            # and the app is currently marked as incomplete, put it into the
            # re-review queue.
            if (not toggling and is_now_paid and
                    addon.status == amo.STATUS_NULL and
                    bango_account_list_form.cleaned_data.get('accounts')):

                log.info(u'[Webapp:%s] (Re-review) Public app, free -> paid.' %
                             self.addon)
                RereviewQueue.flag(self.addon, amo.LOG.REREVIEW_FREE_TO_PAID)
                addon.update(status=amo.STATUS_PENDING)

        # If everything happened successfully, give the user a pat on the back.
        if success:
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
         'is_paid': addon.premium_type in amo.ADDON_PREMIUMS,
         'no_paid': cannot_be_paid,
         'incomplete': addon.status == amo.STATUS_NULL,
         # Bango values
         'bango_account_form': forms.BangoPaymentAccountForm(),
         'bango_account_list_form': bango_account_list_form, })


def payments_accounts(request):
    bango_account_form = forms.BangoAccountListForm(user=request.amo_user)
    return jingo.render(
        request, 'developers/payments/includes/bango_accounts.html',
        {'bango_account_list_form': bango_account_form})


@write
@post_required
def payments_accounts_add(request):
    form = forms.BangoPaymentAccountForm(request.POST)
    if not form.is_valid():
        resp = "\n".join(u'<div><span>%s:</span> %s</div>' %
                             (field.label, field.errors)
                         for field in form if field.errors)
        return http.HttpResponse(resp, status=400)

    data = form.cleaned_data
    try:
        models.BangoPaymentAccount.create(request.amo_user, data)
    except client.Error as e:
        log.error('Error creating Bango payment account; %s' % e)
        return http.HttpResponse(
            _(u'Could not connect to payment server.'), status=400)
    return redirect('mkt.developers.bango.payment_accounts')


@write
@post_required
def payments_accounts_delete(request, id):
    get_object_or_404(models.BangoPaymentAccount, pk=id).cancel()
    return http.HttpResponse('success')

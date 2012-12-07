import uuid

from django.db import models

import commonware.log

import amo
from devhub.models import ActivityLog
from lib.pay_server import client
from users.models import UserForeignKey

log = commonware.log.getLogger('z.devhub')


class SolitudeSeller(amo.models.ModelBase):
    user = UserForeignKey()
    uuid = models.CharField(max_length=255)
    resource_uri = models.CharField(max_length=255)

    class Meta:
        db_table = 'payments_seller'

    @classmethod
    def create(cls, user):
        uuid_ = str(uuid.uuid4())
        # TODO(solitude): This could probably be made asynchronous.
        res = client.post_seller(data={'uuid': uuid_})
        uri = res['resource_uri']
        obj = cls.objects.create(user=user, uuid=uuid_, resource_uri=uri)
        obj.save()

        log.info('[User:%s] Created Solitude seller (uuid:%s)' %
                     (user, uuid_))
        return obj


# Bango

class BangoPaymentAccount(amo.models.ModelBase):
    user = UserForeignKey()
    package_uri = models.CharField(max_length=255)
    name = models.CharField(max_length=64)
    # A soft-delete so we can talk to Solitude asynchronously.
    inactive = models.BooleanField(default=False)

    PACKAGE_VALUES = (
        'adminEmailAddress', 'supportEmailAddress', 'financeEmailAddress',
        'paypalEmailAddress', 'vendorName', 'companyName', 'address1',
        'addressCity', 'addressState', 'addressZipCode', 'addressPhone',
        'countryIso', 'currencyIso', )
    BANK_DETAILS_VALUES = (
        'seller_bango', 'bankAccountPayeeName', 'bankAccountNumber',
        'bankAccountCode', 'bankName', 'bankAddress1', 'bankAddressZipCode',
        'bankAddressIso', )

    class Meta:
        db_table = 'bango_account'
        unique_together = ('user', 'package_uri')

    # TODO(solitude): Make this async.
    @classmethod
    def create(cls, user, form_data):
        # Get the seller object.
        # TODO(solitude): When solitude supports multiple packages per seller,
        # change this to .get_or_create(user). Also write a migration to
        # collapse the SolSel objects to one per user.
        user_seller = SolitudeSeller.create(user)

        # Get the data together for the package creation.
        package_values = dict((k, v) for k, v in form_data.items() if
                              k in cls.PACKAGE_VALUES)
        # TODO: Fill these with better values?
        package_values.setdefault('supportEmailAddress', 'support@example.com')
        package_values.setdefault('paypalEmailAddress', 'nobody@example.com')
        package_values['seller'] = user_seller.resource_uri

        log.info('[User:%s] Creating Bango package' % user)
        res = client.post_package(data=package_values)
        package_uri = res['resource_uri']

        # Get the data together for the bank details creation.
        bank_details_values = dict((k, v) for k, v in form_data.items() if
                                   k in cls.BANK_DETAILS_VALUES)
        bank_details_values['seller_bango'] = package_uri

        log.info('[User:%s] Creating Bango bank details' % user)
        client.post_bank_details(data=bank_details_values)

        obj = cls.objects.create(user=user, package_uri=package_uri,
                                 name=form_data['account_name'])

        log.info('[User:%s] Created Bango payment account (uri: %s)' %
                     (user, package_uri))
        return obj

    def cancel(self):
        self.update(inactive=True)
        log.info('[1@None] Soft-deleted Bango payment account (uri: %s)' %
                     self.package_uri)
        # TODO(solitude): Once solitude supports CancelPackage, that goes here.
        # ...also, make it a (celery) task.

        # We would otherwise have delete(), but we don't want to do that
        # without CancelPackage-ing. Once that support is added, we can write a
        # migration to re-cancel and hard delete the inactive objects.

    def __unicode__(self):
        return '%s - %s' % (self.created.strftime('%m/%y'), self.name)


class AddonBangoPaymentAccount(amo.models.ModelBase):
    addon = models.OneToOneField('addons.Addon',
                                 related_name='addonbangoconfig')
    bango_account = models.ForeignKey(BangoPaymentAccount)

    class Meta:
        db_table = 'addon_bango'
        unique_together = ('addon', 'bango_account')

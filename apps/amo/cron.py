import calendar
import json
import re
import time
import urllib2
from datetime import datetime, timedelta
from subprocess import Popen, PIPE

from django.conf import settings
from django.db.models import Count

from celery.messaging import establish_connection
from celeryutils import task
import cronjobs
import commonware.log
import phpserialize
import redisutils

import amo
from amo.utils import chunked
from addons.models import Addon, AddonCategory, BlacklistedGuid, Category
from addons.utils import AdminActivityLogMigrationTracker, MigrationTracker
from applications.models import Application, AppVersion
from bandwagon.models import Collection
from cake.models import Session
from devhub.models import ActivityLog, LegacyAddonLog
from editors.models import EventLog
from files.models import TestResultCache
from reviews.models import Review
from sharing import SERVICES_LIST
from stats.models import AddonShareCount, Contribution
from users.models import UserProfile

log = commonware.log.getLogger('z.cron')


# TODO(davedash): Delete me after this has been run.
@cronjobs.register
def remove_extra_cats():
    """
    Remove 'misc' category if other categories are present.
    Remove categories in excess of two categories.
    """
    # Remove misc categories from addons if they are also in other categories
    # for that app.
    for cat in Category.objects.filter(misc=True):
        # Find all the add-ons in this category.
        addons_in_misc = cat.addon_set.values_list('id', flat=True)
        delete_me = []

        # Count the categories they have per app.
        cat_count = (AddonCategory.objects.values('addon')
                     .annotate(num_cats=Count('category'))
                     .filter(num_cats__gt=1, addon__in=addons_in_misc,
                             category__application=cat.application_id))

        delete_me = [item['addon'] for item in cat_count]
        log.info('Removing %s from %d add-ons' % (cat, len(delete_me)))
        (AddonCategory.objects.filter(category=cat, addon__in=delete_me)
         .delete())

    with establish_connection() as conn:
        # Remove all but 2 categories from everything else, per app
        for app in amo.APP_USAGE:
            # SELECT
            #   `addons_categories`.`addon_id`,
            #   COUNT(`addons_categories`.`category_id`) AS `num_cats`
            # FROM
            #   `addons_categories` INNER JOIN `categories` ON
            #   (`addons_categories`.`category_id` = `categories`.`id`)
            # WHERE
            #   (`categories`.`application_id` = 1 )
            # GROUP BY
            #   `addons_categories`.`addon_id`
            # HAVING COUNT(`addons_categories`.`category_id`) > 2
            log.info('Examining %s add-ons' % unicode(app.pretty))
            results = (AddonCategory.objects
                       .filter(category__application=app.id)
                       .values('addon_id').annotate(num_cats=Count('category'))
                       .filter(num_cats__gt=2))
            for chunk in chunked(results, 100):
                _trim_categories.apply_async(args=[chunk, app.id],
                                             connection=conn)


@task
def _trim_categories(results, app_id, **kw):
    """
    `results` is a list of dicts.  E.g.:

    [{'addon_id': 138L, 'num_cats': 4}, ...]
    """
    log.info('[%s@%s] Trimming category-fat add-ons' %
             (len(results), _trim_categories.rate_limit))

    delete_me = []
    pks = [r['addon_id'] for r in results]

    for addon in Addon.objects.filter(pk__in=pks):
        qs = addon.addoncategory_set.filter(category__application=app_id)[2:]
        delete_me.extend(qs.values_list('id', flat=True))

    log.info('Deleting %d add-on categories.' % len(delete_me))
    AddonCategory.objects.filter(pk__in=delete_me).delete()


@cronjobs.register
def gc(test_result=True):
    """Site-wide garbage collections."""

    days_ago = lambda days: datetime.today() - timedelta(days=days)
    one_hour_ago = datetime.today() - timedelta(hours=1)

    log.debug('Collecting data to delete')

    logs = (ActivityLog.objects.filter(created__lt=days_ago(90))
            .exclude(action__in=amo.LOG_KEEP).values_list('id', flat=True))

    # Paypal only keeps retrying to verify transactions for up to 3 days. If we
    # still have an unverified transaction after 6 days, we might as well get
    # rid of it.
    contributions_to_delete = (Contribution.objects
            .filter(transaction_id__isnull=True, created__lt=days_ago(6))
            .values_list('id', flat=True))

    collections_to_delete = (Collection.objects.filter(
            created__lt=days_ago(2), type=amo.COLLECTION_ANONYMOUS)
            .values_list('id', flat=True))

    # Remove Incomplete add-ons older than 4 days.
    addons_to_delete = (Addon.objects.filter(
                        highest_status=amo.STATUS_NULL, status=amo.STATUS_NULL,
                        created__lt=days_ago(4))
                        .values_list('id', flat=True))

    with establish_connection() as conn:
        for chunk in chunked(logs, 100):
            _delete_logs.apply_async(args=[chunk], connection=conn)
        for chunk in chunked(contributions_to_delete, 100):
            _delete_stale_contributions.apply_async(
                    args=[chunk], connection=conn)
        for chunk in chunked(collections_to_delete, 100):
            _delete_anonymous_collections.apply_async(
                    args=[chunk], connection=conn)
        for chunk in chunked(addons_to_delete, 100):
            _delete_incomplete_addons.apply_async(
                    args=[chunk], connection=conn)

    log.debug('Cleaning up sharing services.')
    AddonShareCount.objects.exclude(
            service__in=[s.shortname for s in SERVICES_LIST]).delete()

    log.debug('Cleaning up cake sessions.')
    # cake.Session uses Unix Timestamps
    two_days_ago = calendar.timegm(days_ago(2).utctimetuple())
    Session.objects.filter(expires__lt=two_days_ago).delete()

    log.debug('Cleaning up test results cache.')
    TestResultCache.objects.filter(date__lt=one_hour_ago).delete()

    log.debug('Cleaning up test results extraction cache.')
    if settings.NETAPP_STORAGE and settings.NETAPP_STORAGE != '/':
        cmd = ('find', settings.NETAPP_STORAGE, '-maxdepth', '1', '-name',
               'validate-*', '-mtime', '+7', '-type', 'd',
               '-exec', 'rm', '-rf', "{}", ';')

        output = Popen(cmd, stdout=PIPE).communicate()[0]

        for line in output.split("\n"):
            log.debug(line)

    else:
        log.warning('NETAPP_STORAGE not defined.')

    if settings.PACKAGER_PATH:
        log.debug('Cleaning up old packaged add-ons.')

        cmd = ('find', settings.PACKAGER_PATH,
               '-name', '*.zip', '-mtime', '+1', '-type', 'f',
               '-exec', 'rm', '{}', ';')
        output = Popen(cmd, stdout=PIPE).communicate()[0]

        for line in output.split("\n"):
            log.debug(line)

    if settings.COLLECTIONS_ICON_PATH:
        log.debug('Cleaning up uncompressed icons.')

        cmd = ('find', settings.COLLECTIONS_ICON_PATH,
               '-name', '*__unconverted', '-mtime', '+1', '-type', 'f',
               '-exec', 'rm', '{}', ';')
        output = Popen(cmd, stdout=PIPE).communicate()[0]

        for line in output.split("\n"):
            log.debug(line)

    if settings.USERPICS_PATH:
        log.debug('Cleaning up uncompressed userpics.')

        cmd = ('find', settings.USERPICS_PATH,
               '-name', '*__unconverted', '-mtime', '+1', '-type', 'f',
               '-exec', 'rm', '{}', ';')
        output = Popen(cmd, stdout=PIPE).communicate()[0]

        for line in output.split("\n"):
            log.debug(line)


@task
def _delete_logs(items, **kw):
    log.info('[%s@%s] Deleting logs' % (len(items), _delete_logs.rate_limit))
    ActivityLog.objects.filter(pk__in=items).exclude(
            action__in=amo.LOG_KEEP).delete()


@task
def _delete_stale_contributions(items, **kw):
    log.info('[%s@%s] Deleting stale collections' %
             (len(items), _delete_stale_contributions.rate_limit))
    Contribution.objects.filter(
            transaction_id__isnull=True, pk__in=items).delete()


@task
def _delete_anonymous_collections(items, **kw):
    log.info('[%s@%s] Deleting anonymous collections' %
             (len(items), _delete_anonymous_collections.rate_limit))
    Collection.objects.filter(type=amo.COLLECTION_ANONYMOUS,
                              pk__in=items).delete()


@task
def _delete_incomplete_addons(items, **kw):
    log.info('[%s@%s] Deleting incomplete add-ons' %
             (len(items), _delete_incomplete_addons.rate_limit))
    for addon in Addon.objects.filter(
            highest_status=0, status=0, pk__in=items):
        try:
            addon.delete('Deleted for incompleteness')
        except Exception as e:
            log.error("Couldn't delete add-on %s: %s" % (addon.id, e))


@cronjobs.register
def migrate_admin_logs():
    # Get the highest id we've looked at.
    a = AdminActivityLogMigrationTracker()
    id = a.get() or 0

    # filter here for addappversion
    items = LegacyAddonLog.objects.filter(
            type=amo.LOG.ADD_APPVERSION.id, pk__gt=id).values_list(
            'id', flat=True)
    for chunk in chunked(items, 100):
        _migrate_admin_logs.delay(chunk)
        a.set(chunk[-1])


@task
def _migrate_admin_logs(items, **kw):
    print 'Processing: %d..%d' % (items[0], items[-1])
    for item in LegacyAddonLog.objects.filter(pk__in=items):
        kw = dict(user=item.user, created=item.created)
        amo.log(amo.LOG.ADD_APPVERSION, (Application, item.object1_id),
                (AppVersion, item.object2_id), **kw)


# TODO(davedash): remove after /editors is on zamboni
@cronjobs.register
def migrate_editor_eventlog():
    a = MigrationTracker('eventlog')
    id = a.get() or 0

    items = EventLog.objects.filter(type='editor', pk__gt=id).values_list(
            'id', flat=True)

    for chunk in chunked(items, 100):
        _migrate_editor_eventlog(chunk)
        a.set(chunk[-1])


@task
def _migrate_editor_eventlog(items, **kw):
    log.info('[%s@%s] Migrating eventlog items' %
             (len(items), _migrate_editor_eventlog.rate_limit))
    for item in EventLog.objects.filter(pk__in=items):
        kw = dict(user=item.user, created=item.created)
        if item.action == 'review_delete':
            details = None
            try:
                details = phpserialize.loads(item.notes)
            except ValueError:
                pass
            amo.log(amo.LOG.DELETE_REVIEW, item.changed_id, details=details,
                    **kw)
        elif item.action == 'review_approve':
            try:
                r = Review.objects.get(pk=item.changed_id)
                amo.log(amo.LOG.ADD_REVIEW, r, r.addon, **kw)
            except Review.DoesNotExist:
                log.warning("Couldn't find review for %d" % item.changed_id)


@cronjobs.register
def dissolve_outgoing_urls():
    """Over time, some outgoing.m.o URLs have been encoded several times in the
    db.  This removes the layers of encoding and sets URLs to their real value.
    The app will take care of sending things through outgoing.m.o.  See bug
    608117."""

    needle = 'outgoing.mozilla.org'

    users = (UserProfile.objects.filter(homepage__contains=needle)
             .values_list('id', 'homepage'))

    if not users:
        print "Didn't find any add-ons with messed up homepages."
        return

    print 'Found %s users to fix.  Sending them to celeryd.' % len(users)

    for chunk in chunked(users, 100):
        _dissolve_outgoing_urls.delay(chunk)


@task(rate_limit='60/h')
def _dissolve_outgoing_urls(items, **kw):
    log.info('[%s@%s] Dissolving outgoing urls' %
             (len(items), _dissolve_outgoing_urls.rate_limit))

    regex = re.compile('^http://outgoing.mozilla.org/v1/[0-9a-f]+/(.*?)$')

    def peel_the_onion(url):
        match = regex.match(url)

        if not match:
            return None

        new = urllib2.unquote(match.group(1))
        are_we_there_yet = peel_the_onion(new)  # That's right. You love it.

        if not are_we_there_yet:
            return new
        else:
            return are_we_there_yet

    for user in items:
        url = peel_the_onion(user[1])

        # 20 or so of these are just to outgoing.m.o, so just whack them
        if url == 'http://outgoing.mozilla.org':
            url = None

        UserProfile.objects.filter(pk=user[0]).update(homepage=url)


# TODO(davedash): Remove after 5.12.7 is pushed.
@cronjobs.register
def activity_log_scrubber():
    """
    Scans activity log for REMOVE_FROM_COLLECTION and ADD_TO_COLLECTION, looks
    for collections in arguments and checks whether collection is listed.
    """

    items = (ActivityLog.objects.filter(
             action__in=[amo.LOG.ADD_TO_COLLECTION.id,
                         amo.LOG.REMOVE_FROM_COLLECTION.id])
             .values('id', '_arguments'))
    ids = []
    count = 0
    # ~127K
    for item in items:
        count += 1
        for k in json.loads(item['_arguments']):
            if 'bandwagon.collection' not in k:
                continue
            if not all(Collection.objects.filter(pk=k.values()[0])
                       .values_list('listed', flat=True)):
                log.debug('%d items seen.' % count)
                ids.append(item['id'])
        if len(ids) > 100:
            _activity_log_scrubber.delay(ids)
            ids = []

    # get everyone else
    _activity_log_scrubber.delay(ids)


@task(rate_limit='60/h')
def _activity_log_scrubber(items, **kw):
    log.info('[%s@%s] Deleting activity log items' %
             (len(items), _activity_log_scrubber.rate_limit))

    ActivityLog.objects.filter(id__in=items).delete()


class QueueCheck(object):
    key = 'cron:queuecheck:%s:%s'

    def __init__(self):
        self.redis = redisutils.connections['master']

    def queues(self):
        # Figure out all the queues we're using. celery is the default, with a
        # warning threshold of 10 minutes.
        queues = {'celery': 60 * 60}
        others = set(r['queue'] for r in settings.CELERY_ROUTES.values())
        # 30 second threshold for the fast queues.
        queues.update((q, 30) for q in others)
        return queues

    def set(self, action, queue):
        self.redis.set(self.key % (action, queue), time.time())

    def get(self, action, queue):
        return self.redis.get(self.key % (action, queue))


@cronjobs.register
def check_queues():
    checker = QueueCheck()
    for queue in checker.queues():
        checker.set('ping', queue)
        ping.apply_async(queue=queue, routing_key=queue, exchange=queue)


@task
def ping(**kw):
    queue = kw['delivery_info']['routing_key']
    log.info('[1@None] Checking the %s queue' % queue)
    QueueCheck().set('pong', queue)


# TODO(andym): remove this once they are all gone.
@cronjobs.register
def delete_brand_thunder_addons():
    ids = (102188, 102877, 103381, 103382, 103388, 107864, 109233, 109242,
           111144, 111145, 115970, 150367, 146373, 143547, 142886, 140931,
           113511, 100304, 130876, 126516, 124495, 123900, 120683, 159626,
           159625, 157780, 157776, 155494, 155489, 155488, 152740, 152739,
           151187, 193275, 184048, 182866, 179429, 179426, 161783, 161781,
           161727, 160426, 160425, 220155, 219726, 219724, 219723, 219722,
           218413, 200756, 200755, 199904, 221522, 221521, 221520, 221513,
           221509, 221508, 221505, 220882, 220880, 220879, 223384, 223383,
           223382, 223381, 223380, 223379, 223378, 223376, 222194, 221524,
           223403, 223402, 223400, 223399, 223398, 223388, 223387, 223386,
           223385, 232687, 232681, 228394, 228393, 228392, 228391, 228390,
           226428, 226427, 226388, 235892, 235836, 235277, 235276, 235274,
           232709, 232708, 232707, 232694, 232688, 94461, 94452, 54288, 50418,
           49362, 49177, 239113, 102186, 102185, 101166, 101165, 101164,
           99010, 99007, 99006, 98429, 98428, 45834, 179542, 103383)
    guids = (
'umespersona_at_brandthunder.com', 'vanderbiltupersona_at_brandthunder.com',
'michiganstupersona_at_brandthunder.com', 'acconfpersona_at_brandthunder.com',
'uofarizonapersona_at_brandthunder.com', 'uofcincinnatipersona_at_brandthunder.com',
'texastechupersona_at_brandthunder.com', 'uofkansaspersona_at_brandthunder.com',
'uofpittsburghpersona_at_brandthunder.com', 'uofgeorgiapersona_at_brandthunder.com',
'halloween2010persona_at_brandthunder.com', 'halloweenpersona_at_brandthunder.com',
'uofscarolinapersona_at_brandthunder.com', 'auburnupersona_at_brandthunder.com',
'georgetownupersona_at_brandthunder.com', 'ncstateupersona_at_brandthunder.com',
'uofmissouripersona_at_brandthunder.com', 'uoftennesseepersona_at_brandthunder.com',
'washingtonstupersona_at_brandthunder.com',
'uofnotredamepersona_at_brandthunder.com',
'nasapersona_at_brandthunder.com', 'uofmichiganpersona_at_brandthunder.com',
'villanovaupersona_at_brandthunder.com', 'uofillinoispersona_at_brandthunder.com',
'oklahomastupersona_at_brandthunder.com', 'uofwisconsinpersona_at_brandthunder.com',
'uofwashingtonpersona_at_brandthunder.com', 'uclapersona_at_brandthunder.com',
'arizonastupersona_at_brandthunder.com', 'uofncarolinapersona_at_brandthunder.com',
'bigtenconfpersona_at_brandthunder.com', 'indianaupersona_at_brandthunder.com',
'purdueupersona_at_brandthunder.com', 'pennstupersona_at_brandthunder.com',
'uoflouisvillepersona_at_brandthunder.com', 'marquetteupersona_at_brandthunder.com',
'uofiowapersona_at_brandthunder.com', 'wakeforestunivpersona_at_brandthunder.com',
'stanfordupersona_at_brandthunder.com', 'providencecollpersona_at_brandthunder.com',
'kansasstupersona_at_brandthunder.com', 'uoftexaspersona_at_brandthunder.com',
'uofcaliforniapersona_at_brandthunder.com', 'oregonstupersona_at_brandthunder.com',
'gatechpersona_at_brandthunder.com', 'depaulupersona_at_brandthunder.com',
'uofalabamapersona_at_brandthunder.com', 'stjohnsupersona_at_brandthunder.com',
'uofmiamipersona_at_brandthunder.com', 'flastatepersona_at_brandthunder.com',
'uofconnecticutpersona_at_brandthunder.com',
'uofoklahomapersona_at_brandthunder.com',
'baylorupersona_at_brandthunder.com', 'stackpersona_at_brandthunder.com',
'askmenboom_at_askmen.com', 'uscpersona_at_brandthunder.com',
'redbullspersona_at_brandthunder.com', 'huffpostpersona_at_brandthunder.com',
'mlsunionpersona_at_brandthunder.com', 'goblinspersona2_at_brandthunder.com',
'ignboom_at_ign.com', 'fantasyrpgtheme_at_brandthunder.com',
'dragontheme_at_brandthunder.com', 'animetheme_at_brandthunder.com',
'sanjeevkapoorboom_at_sanjeevkapoor.com', 'godukeboom_at_goduke.com',
'nbakingsboom_at_nba.com', 'prowrestlingboom_at_brandthunder.com',
'plaidthemetheme_at_brandthunder.com', 'fleurdelistheme_at_brandthunder.com',
'snowthemetheme_at_brandthunder.com', 'transparenttheme_at_brandthunder.com',
'nauticaltheme_at_brandthunder.com', 'sierrasunsettheme_at_brandthunder.com',
'hotgirlbodytheme_at_brandthunder.com', 'ctrlaltdelboom_at_cad-comic.com',
'cricketboom_at_brandthunder.com', 'starrynighttheme_at_brandthunder.com',
'fantasyflowertheme_at_brandthunder.com', 'militarycamotheme_at_brandthunder.com',
'paristhemetheme_at_brandthunder.com', 'greatwalltheme_at_brandthunder.com',
'motorcycle_at_brandthunder.com', 'fullspeedboom_at_fullspeed2acure.com',
'waterfalls_at_brandthunder.com', 'mothersday2010boom_at_brandthunder.com',
'pyramids_at_brandthunder.com', 'mountain_at_brandthunder.com',
'beachsunset_at_brandthunder.com', 'newyorkcity_at_brandthunder.com',
'shinymetal_at_brandthunder.com', 'moviepremiereboom_at_brandthunder.com',
'kitttens_at_brandthunder.com', 'tulips_at_brandthunder.com',
'aquarium_at_brandthunde.com',  # [sic]
'wood_at_brandthunder.com', 'puppies_at_brandthunder.com', 'ouaboom_at_oua.ca',
'wibwboom_at_wibw.com', 'nasasettingsun_at_brandthunder.com',
'bluesky_at_brandthunder.com',
'cheerleaders_at_brandthunder.com', 'greengrass_at_brandthunder.com',
'crayonpinktheme_at_brandthunder.com', 'crayonredtheme_at_brandthunder.com',
'crayonyellow_at_brandthunder.com', 'crayongreen_at_brandthunder.com',
'crayonblue_at_brandthunder.com', 'weatherboom_at_brandthunder.com',
'crayonblack_at_brandthunder.com', 'ambientglow_at_brandthunder.com',
'bubbles_at_brandthunder.com', 'matrixcode_at_brandthunder.com',
'firetheme_at_brandthunder.com', 'neonlights_at_brandthunder.com',
'brushedmetal_at_brandthunder.com', 'sugarland2_at_brandthunder.com',
'suns2_at_brandthunder.com', 'thanksgiving2_at_brandthunder.com',
'ecoboom2_at_brandthunder.com', 'thanksgivingboom_at_brandthunder.com')

    guids = [guid.replace('_at_', '@') for guid in guids]
    # This is a bit of an atomic bomb approach, but should ensure
    # that no matter what the state of the guids or addons on AMO.
    # We will end up with no addons or guids relating to Brand Thunder.
    #
    # Clean out any that may exist prior to deleting addons (was causing
    # errors on preview).
    blacklist = BlacklistedGuid.uncached.filter(guid__in=guids)
    log.info('Found %s guids to delete (bug 636834)'
             % blacklist.count())
    blacklist.delete()
    addons = Addon.uncached.filter(pk__in=ids)
    log.info('Found %s addons to delete (bug 636834)' % addons.count())
    for addon in addons:
        try:
            log.info('About to delete addon %s (bug 636834)' % addon.id)
            addon.delete('Deleting per Brand Thunder request (bug 636834).')
        except:
            log.error('Could not delete add-on %d (bug 636834)' % addon.id,
                      exc_info=True)
    # Then clean out any remaining blacklisted guids after being run.
    blacklist = BlacklistedGuid.uncached.filter(guid__in=guids)
    log.info('Found %s guids to delete (bug 636834)'
             % blacklist.count())
    blacklist.delete()

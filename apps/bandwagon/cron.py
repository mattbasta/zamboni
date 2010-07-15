import datetime
import itertools

from django.db import connection, connections, transaction
from django.db.models import Count

import commonware.log
import multidb
from celery.decorators import task
from celery.messaging import establish_connection

from amo.utils import chunked, slugify
from bandwagon.models import (Collection,
                              CollectionSubscription,
                              CollectionVote)
import cronjobs

task_log = commonware.log.getLogger('z.task')


@cronjobs.register
def update_collections_subscribers():
    """Update collections subscribers totals."""

    d = (CollectionSubscription.objects.values('collection_id')
         .annotate(count=Count('collection'))
         .extra(where=['DATE(created)=%s'], params=[datetime.date.today()]))

    with establish_connection() as conn:
        for chunk in chunked(d, 1000):
            _update_collections_subscribers.apply_async(args=[chunk],
                                                        connection=conn)


@task(rate_limit='15/m')
def _update_collections_subscribers(data, **kw):
    task_log.info("[%s@%s] Updating collections' subscribers totals." %
                   (len(data), _update_collections_subscribers.rate_limit))
    cursor = connection.cursor()
    today = datetime.date.today()
    for var in data:
        q = """REPLACE INTO
                    stats_collections(`date`, `name`, `collection_id`, `count`)
                VALUES
                    (%s, %s, %s, %s)"""
        p = [today, 'new_subscribers', var['collection_id'], var['count']]
        cursor.execute(q, p)
    transaction.commit_unless_managed()


@cronjobs.register
def update_collections_votes():
    """Update collection's votes."""

    up = (CollectionVote.objects.values('collection_id')
          .annotate(count=Count('collection'))
          .filter(vote=1)
          .extra(where=['DATE(created)=%s'], params=[datetime.date.today()]))

    down = (CollectionVote.objects.values('collection_id')
            .annotate(count=Count('collection'))
            .filter(vote=-1)
            .extra(where=['DATE(created)=%s'], params=[datetime.date.today()]))

    with establish_connection() as conn:
        for chunk in chunked(up, 1000):
            _update_collections_votes.apply_async(args=[chunk, "new_votes_up"],
                                                  connection=conn)
        for chunk in chunked(down, 1000):
            _update_collections_votes.apply_async(args=[chunk,
                                                        "new_votes_down"],
                                                  connection=conn)


@task(rate_limit='15/m')
def _update_collections_votes(data, stat, **kw):
    task_log.info("[%s@%s] Updating collections' votes totals." %
                   (len(data), _update_collections_votes.rate_limit))
    cursor = connection.cursor()
    for var in data:
        q = ('REPLACE INTO stats_collections(`date`, `name`, '
             '`collection_id`, `count`) VALUES (%s, %s, %s, %s)')
        p = [datetime.date.today(), stat,
             var['collection_id'], var['count']]
        cursor.execute(q, p)
    transaction.commit_unless_managed()


# TODO: remove this once zamboni enforces slugs.
@cronjobs.register
def collections_add_slugs():
    """Give slugs to any slugless collections."""
    q = Collection.objects.filter(slug=None)
    ids = q.values_list('id', flat=True)
    task_log.info('%s collections without names' % len(ids))
    max_length = Collection._meta.get_field('slug').max_length
    cnt = itertools.count()
    # Chunk it so we don't do huge queries.
    for chunk in chunked(ids, 300):
        for c in q.no_cache().filter(id__in=chunk):
            c.slug = c.nickname or slugify(c.name)[:max_length]
            if not c.slug:
                c.slug = 'collection'
            c.save(force_update=True)
            task_log.info(u'%s. %s => %s' % (next(cnt), c.name, c.slug))

    # Uniquify slug names by user.
    cursor = connections[multidb.get_slave()].cursor()
    dupes = cursor.execute("""
        SELECT user_id, slug FROM (
            SELECT user_id, slug, COUNT(1) AS cnt
            FROM collections c INNER JOIN collections_users cu
              ON c.id = cu.collection_id
            GROUP BY user_id, slug) j
        WHERE j.cnt > 1""")
    task_log.info('Uniquifying %s (user, slug) pairs' % dupes)
    cnt = itertools.count()
    for user, slug in cursor.fetchall():
        q = Collection.objects.filter(slug=slug, collectionuser__user=user)
        # Skip the first one since it's unique without any appendage.
        for idx, c in enumerate(q[1:]):
            # Give enough space for appending a two-digit number.
            slug = c.slug[:max_length - 3]
            c.slug = u'%s-%s' % (slug, idx + 1)
            c.save(force_update=True)
            task_log.info(u'%s. %s => %s' % (next(cnt), slug, c.slug))

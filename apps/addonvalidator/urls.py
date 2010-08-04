from django.conf.urls.defaults import patterns, url, include
from django.shortcuts import redirect

from . import views

urlpatterns = patterns('',
    (r'^$', 'addonvalidator.views.index'),
    (r'^save/$', 'addonvalidator.views.save'),
    (r'^status/(?P<task_id>.{36})/$',
        'addonvalidator.views.status'),
    (r'^status/(?P<task_id>.{36})/poll$',
        'addonvalidator.views.poll'),
    (r'^result/(?P<task_id>.{36})$',
        'addonvalidator.views.result')
)

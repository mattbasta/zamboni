from django.conf.urls.defaults import patterns, url, include
from django.shortcuts import redirect

from . import views

urlpatterns = patterns('',
    (r'^$', 'validator.views.index'),
    (r'^save/$', 'validator.views.save'),
    (r'^status/$', 'validator.views.status'),
    (r'^poll/$', 'validator.views.poll')
)

    
from django.conf.urls.defaults import patterns, include, url
from . import views

validator_patterns = patterns('',
    url(r'^$', views.status, name="status"),
    url(r'^poll$', views.poll, name="poll"),
)

urlpatterns = patterns('',
    url(r'^/$', views.index, name="validator.upload"),
    url(r'^/save/$', views.save, name="validator.save"),
    (r'^/status/(?P<task_id>.{36})/', include(validator_patterns)),
    url(r'^/result/(?P<task_id>.{36})$', views.result, name="validator.result")
)

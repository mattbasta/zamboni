import hashlib
import random

import json

from django.template import Context, loader
from django.core.context_processors import csrf
from django.http import HttpResponse, Http404, HttpResponseRedirect
import jingo

def index(request):
    "The upload page"
    
    error = None
    if "error" in request.GET:
        error = request.GET["error"]
    
    data = {"error": error}
    return jingo.render(request, 'validator/index.html', data)

def save(request):
    "The page that handles the submitted file"
    
    if "addon" not in request.FILES:
        return  HttpResponseRedirect("/validator?error=upload")
    
    file_ = request.FILES["addon"]
    extension = file_.name.split('.')[-1]
    
    if not _verify_submitted_addon(file_):
        return HttpResponseRedirect("/validator?error=addon")
    
    tempname = hashlib.sha1(str(random.random())).hexdigest()
    tempfile = open("/tmp/%s.%s" % (tempname, extension),
                    "wb+")
    for chunk in file_.chunks():
        tempfile.write(chunk)
    tempfile.close()
    
    return HttpResponseRedirect("/validator/status/?id=%s" % tempname)
    
def status(request):
    template = loader.get_template('validator/status.html')
    
    if "id" not in request.GET:
        return Http404()
    
    context = Context({"id": request.GET["id"]})
    return HttpResponse(template.render(context))
    
def poll(request):
    
    if "id" not in request.GET:
        return Http404()
    
    
    jdata = json.dumps({"status":"queued"});
    
    return HttpResponse(jdata)

def _verify_submitted_addon(addon):
    "Verifies a file that is submitted as an addon"
    
    extension = addon.name.split(".")[-1]
    if extension not in ("xpi", "jar"):
        return False
    
    return True

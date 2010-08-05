import base64
import hashlib
import json
import os
import random

from django.http import HttpResponse, Http404, HttpResponseRedirect
from django.core.cache import cache
from celery.result import BaseAsyncResult
from celery.backends import default_backend
import jingo
from tower import ugettext as _

from addonvalidator.tasks import start_job


def index(request):
    "The upload page"

    error = request.GET.get("error", None)

    data = {"error": error}
    return jingo.render(request, 'validator/upload.html', data)


def save(request):
    "The page that handles the submitted file"

    is_ajax = request.is_ajax()

    if "addon" not in request.FILES:
        if not is_ajax:
            return HttpResponseRedirect("/validator?error=upload")
        else:
            return HttpResponse('{"error":true}')

    # Save the file to the temporary directory

    file_ = request.FILES["addon"]
    extension = os.path.splitext(file_.name)[-1]

    if not _verify_submitted_addon(file_):
        return HttpResponseRedirect("/validator?error=addon")

    # Give the job a local ID
    tempname = hashlib.sha1(str(random.random())).hexdigest()
    temppath = "/tmp/%s.%s" % (tempname, extension)
    tempfile = open(temppath, "wb+")

    # If it's an AJAX request, it's Base64 encoded. Remove the Data URL header
    # and pass the rest as part of a buffer.
    if is_ajax:
        found_start = False
        while not found_start:
            data = file_.read(1)
            if not data or data == ",":
                break

    # Copy the file over in chunks of 65kb.
    while True:

        chunk = file_.read(24 * 1024)
        if not chunk:
            break
        # AJAX passes everything in data url format, which is B64 encoded.
        if is_ajax:
            chunk = base64.b64decode(chunk)
        tempfile.write(chunk)
    tempfile.close()

    # Put the job in motion by registering it with Celery.
    job = start_job.delay(temppath)
    task_id = job.task_id
    #task_id = "whatever"

    # Tell the client to go to the status page.
    destination = "/validator/status/%s" % task_id
    if "ajax" in request.GET:
        return HttpResponse(destination)
    else:
        return HttpResponseRedirect(destination)


def _verify_submitted_addon(addon):
    "Verifies a file that is submitted as an addon"

    extension = os.path.splitext(addon.name)[-1]
    if extension not in ("xpi", "jar"):
        return False

    return True


def status(request, task_id):
    "The status page for a given task."
    data = {"task": task_id}
    return jingo.render(request, 'validator/status.html', data)


def poll(request, task_id):
    "A request about the progress of a currently running task"
    result = BaseAsyncResult(task_id, default_backend)
    #statuses = {"": ""}

    if result.successful():
        # Completed validation, might be saving to cache...
        save_id = result.result
        save_result = BaseAsyncResult(save_id, default_backend)
        if save_result.successful():
            data = {"status": "done"}
        else:
            # Consider what the task status pairs up with.
            statuses = {"PENDING": "queued",
                        "STARTED": "working"}
            if result.status in statuses:
                data = {"status": statuses[result.status]}
            else:
                data = {"status": "done"}
    else:
        data = {"status": "queued"}

    jdata = json.dumps(data)

    return HttpResponse(jdata)


def result(request, task_id):
    "Displays the result of the validation"

    addon_results = cache.get(task_id)

    # If the result set doesn't exist, then don't humor the user.
    if addon_results is None:
        raise Http404()

    results_json = json.loads(addon_results)
    types = {"unknown": _("Unknown"),
             "extension": _("Extension"),
             "theme": _("Theme"),
             "dictionary": _("Dictionary"),
             "langpack": _("Language Pack"),
             "search": _("Search Provider")}

    tree = results_json["message_tree"]

    errors = results_json["errors"]
    warnings = results_json["warnings"]
    infos = results_json["infos"]

    single_type = bool(errors) ^ bool(warnings) ^ bool(infos)

    data = {"val_msgs": results_json["messages"],
            "rejected": results_json["rejected"],
            "success": results_json["success"],
            "type": types[results_json["detected_type"]],
            "id": task_id,
            "warnings": warnings,
            "errors": errors,
            "infos": infos,
            "tree": tree,
            "use_ids": (errors + warnings + infos) > 2,
            "single_type": single_type}
    return jingo.render(request, 'validator/result.html', data)

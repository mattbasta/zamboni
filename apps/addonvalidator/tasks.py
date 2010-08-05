import os
from StringIO import StringIO
import json
from django.core.cache import cache
from celeryutils import task

@task(ignore_result=False)
def start_job(path, task_id=None):
    "Starts the validation process for an addon."

    import validator
    from validator import main
    from validator.constants import PACKAGE_ANY
    from validator.errorbundler import ErrorBundle

    results = StringIO()

    # Create an empty error report for the package with no fancy output.
    eb = ErrorBundle(results, True)
    # The value here should match Remora's ($status == STATUS_LISTED)
    eb.save_resource("listed", True)
    validator.main.prepare_package(eb,
                                   path,
                                   PACKAGE_ANY)

    eb.print_json()
    output = results.getvalue()

    subjob = save_job_results.delay(task_id, path, output)
    return subjob.task_id


@task(ignore_result=False)
def save_job_results(id, path, output):
    "Saves the results of a job to the cache."

    cache.set(id, output)
    # And finally, clean up after yourself!
    os.remove(path)

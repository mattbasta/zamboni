import jinja2
from tower import ugettext as _
from jingo import register, env


@register.filter
@jinja2.contextfilter
def print_file(context, filename, line=None):
    "Prints a message's file path."

    if isinstance(filename, (list, tuple)):
        if filename[-1] == '':
            filename[-1] = _("(no file)")
        output = "%s"
        for name in filename:
            output = output % ('<span class="line">%s%%s</span>' % name)
        if line is not None:
            output = output % " @ Line %s" % line
        else:
            output = output % ""

        return jinja2.Markup(output)
    else:
        if not filename:
            return "(no file)"
        return filename


@register.filter
@jinja2.contextfilter
def print_description(context, description):
    "Prints a message's file path."

    if isinstance(description, list):
        output = []
        for line in description:
            output.append(_(line))

        return jinja2.Markup("</p><p>".join(output))
    else:
        return description


@register.function
def build_visibilitytree(tree, prefix=""):
    "Builds out that cute little check box tree on the results page"
    output = []

    t = env.get_template('validator/visibilitytree.html')
    for key, value in tree.items():
        if key.startswith("__"):
            continue

        markup = t.render(key=key,
                          value=value,
                          prefix=prefix,
                          errors=value["__errors"],
                          warnings=value["__warnings"],
                          infos=value["__infos"],
                          messages=value["__messages"])
        output.append(markup)

    return jinja2.Markup("\n".join(output))


@register.function
def result_class(rejected, success):
    "Returns a class for the box that describes whether we succeeded or not."
    
    if rejected:
        return "rejected"
    elif not success:
        return "errors"


@register.function
def translate_name(name):
    translation = {"chromemanifest": "Chrome Manifest",
                   "main": "General Tests",
                   "rdf": "RDF Tests",
                   "typedetection": "Add-on Type Detection",
                   "xpi": "XPI Parser",
                   "testcases_conduit": "Conduit Testing",
                   "testcases_content": "Package Content",
                   "testcases_installrdf": "install.rdf Tests",
                   "testcases_l10ncompleteness": "L10n Completeness",
                   "testcases_langpack": "Language Pack Tests",
                   "testcases_library_blacklist": "Library Blacklists",
                   "testcases_packagelayout": "Package Layout",
                   "testcases_targetapplications": "Target Application Tests",
                   "testcases_themes": "Theme Tests",
                   "testcases_l10n_dtd": "DTD File Tests",
                   "testcases_l10n_properties": "Properties File Tests",
                   "testcases_markup_csstester": "CSS Tests",
                   "testcases_markup_markuptester": "Markup Tests"}
    if name in translation:
        return _(translation[name])
    return name

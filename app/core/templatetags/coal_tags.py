from django import template
from django.utils.html import format_html
from django.utils.http import urlencode

register = template.Library()


@register.simple_tag(takes_context=True)
def querystring(context, **kwargs):
    request = context.get("request")
    if request is not None:
        params = request.GET.copy()
    else:
        params = {}
    for key, value in kwargs.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    qs = urlencode(params, doseq=True)
    return format_html("?{}", qs) if qs else ""


@register.filter
def dict_get(d, key):
    if isinstance(d, dict):
        return d.get(key, [])
    return []


@register.filter
def in_list(value, lst):
    return value in lst


@register.filter
def key_in_dict(key, d):
    if isinstance(d, dict):
        return key in d
    return False


@register.filter
def get_item(d, key):
    if isinstance(d, dict):
        return d.get(key)
    try:
        return d[key]
    except (KeyError, TypeError):
        return None


@register.filter
def get_field(obj, field_name):
    return getattr(obj, field_name, "")


@register.filter
def endswith(value, suffix):
    return str(value).endswith(suffix)


@register.simple_tag(takes_context=True)
def page_url(context, page_num):
    request = context.get("request")
    if request is not None:
        params = request.GET.copy()
    else:
        params = {}
    params["page"] = page_num
    return "?" + urlencode(params, doseq=True)


@register.inclusion_tag("partials/pagination.html", takes_context=True)
def pagination(context, page_obj):
    paginator = page_obj.paginator
    current = page_obj.number
    total = paginator.num_pages

    delta = 2
    left = max(1, current - delta)
    right = min(total, current + delta)

    pages = []
    if left > 1:
        pages.append(1)
        if left > 2:
            pages.append(None)
    for p in range(left, right + 1):
        pages.append(p)
    if right < total:
        if right < total - 1:
            pages.append(None)
        pages.append(total)

    return {
        "page_obj": page_obj,
        "pages": pages,
        "request": context.get("request"),
    }


@register.filter
def other_field(form, select_field_name):
    other_key = select_field_name.replace("_select", "_other", 1)
    try:
        return form[other_key]
    except KeyError:
        return None

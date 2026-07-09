import logging
import textwrap
from zoneinfo import ZoneInfo

from django.templatetags.static import static
from django.urls import reverse

from jinja2 import (
    Environment,
    ChoiceLoader,
    FileSystemLoader,
    PackageLoader,
    BaseLoader,
)
from django.conf import settings
import pprint
import json


def environment(**options):
    env = Environment(**options)
    env.globals.update(
        {
            "static": static,
            "url": reverse,
            "debug": settings.DEBUG,
            "settings": settings,
        }
    )
    env.filters.update(
        {
            "pprint": lambda v: pprint.pformat(v, indent=4),
            "tojson": lambda v: json.dumps(v, indent=4, sort_keys=True, default=str),
            "textwrap": lambda v, width=80: "\n".join(textwrap.wrap(v, width=width)),
            "berlin": lambda dt, fmt="%d.%m.%Y %H:%M %Z": dt.astimezone(
                ZoneInfo("Europe/Berlin")
            ).strftime(fmt),
        }
    )
    return env


def get_loader():
    loaders: list[BaseLoader] = [FileSystemLoader(settings.TEMPLATES[0]["DIRS"])]

    for app in settings.INSTALLED_APPS:
        try:
            loaders.append(PackageLoader(app, "templates"))
        except Exception:
            logging.warning(f"Failed to load template for app {app}")

    return ChoiceLoader(loaders)

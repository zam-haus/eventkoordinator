import logging

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

from project.jinja_filters import ALL_FILTERS


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
    env.filters.update(ALL_FILTERS)
    return env


def get_loader():
    loaders: list[BaseLoader] = [FileSystemLoader(settings.TEMPLATES[0]["DIRS"])]

    for app in settings.INSTALLED_APPS:
        try:
            loaders.append(PackageLoader(app, "templates"))
        except Exception:
            logging.warning(f"Failed to load template for app {app}")

    return ChoiceLoader(loaders)

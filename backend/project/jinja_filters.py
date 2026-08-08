"""Jinja2 filters shared by the regular template environment and the sandboxed
mail-template environment (``userdefinedmodel.mailtemplates``).

They are registered in both places so the same template source works whether it
is loaded from disk or from a ``MailTemplate`` row.
"""

import datetime
import json
import pprint
import textwrap
from zoneinfo import ZoneInfo

from jinja2 import Undefined
from markupsafe import Markup, escape

DEFAULT_TIMEZONE = "Europe/Berlin"

#: Prefix used by :func:`userinput` to mark quoted user-supplied text.
DEFAULT_USERINPUT_PREFIX = "    "
DEFAULT_USERINPUT_PLACEHOLDER = "(leer / empty)"


def _is_missing(value) -> bool:
    return value is None or isinstance(value, Undefined)


def tz_convert(value, tz: str = DEFAULT_TIMEZONE):
    """Convert ``value`` to a timezone-aware datetime in ``tz``.

    Registered under the filter name ``timezone`` so mails can write
    ``{{ value | timezone("Europe/Berlin") | isoformat() }}``. Returns a
    ``datetime`` (not a string) so it composes with :func:`isoformat`.

    - ``None`` / undefined -> ``None`` (``isoformat`` then yields ``""``)
    - naive datetime -> interpreted in ``settings.TIME_ZONE`` first
    - ``date`` -> returned unchanged; a date has no timezone
    - ``str`` -> parsed with ``datetime.fromisoformat``
    - ``int`` / ``float`` -> POSIX timestamp
    """
    if _is_missing(value):
        return None

    zone = ZoneInfo(tz)

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        value = datetime.datetime.fromisoformat(value)
    elif isinstance(value, bool):
        raise ValueError(f"Cannot convert {value!r} to a datetime")
    elif isinstance(value, (int, float)):
        value = datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)

    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            from django.utils import timezone as django_timezone

            value = django_timezone.make_aware(value)
        return value.astimezone(zone)

    if isinstance(value, datetime.date):
        # A plain date has no time and therefore no meaningful timezone.
        return value

    raise ValueError(f"Cannot convert {value!r} to a datetime")


def isoformat(value, timespec: str = "seconds", sep: str = " ") -> str:
    """Format a date/time as ISO 8601.

    Defaults to second precision and a space separator, which is what reads best
    in a mail. Pass ``sep="T"`` for strict ISO 8601. Strings pass through
    unchanged so the filter is safe to apply twice.
    """
    if _is_missing(value):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=sep, timespec=timespec)
    if isinstance(value, datetime.time):
        return value.isoformat(timespec=timespec)
    if isinstance(value, datetime.date):
        return value.isoformat()
    raise ValueError(f"Cannot format {value!r} as ISO 8601")


def userinput(
    value,
    prefix: str = DEFAULT_USERINPUT_PREFIX,
    placeholder: str = DEFAULT_USERINPUT_PLACEHOLDER,
) -> str:
    """Mark user-supplied text in a plaintext mail by indenting every line.

    Blank lines get ``prefix.rstrip()`` so the mail carries no trailing
    whitespace. Empty input renders the (also indented) ``placeholder`` so the
    reader can tell an empty field from a missing paragraph.
    """
    if _is_missing(value):
        text = ""
    else:
        text = value if isinstance(value, str) else str(value)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    if not text.strip():
        text = placeholder

    return "\n".join(prefix + line if line.strip() else prefix.rstrip() for line in text.split("\n"))


def htmlquote(value) -> Markup:
    """HTML counterpart of :func:`userinput`: escape and turn newlines into ``<br>``.

    Intended for use inside a ``<blockquote class="user-input">`` so template
    authors never need ``|safe`` on user-supplied text.
    """
    if _is_missing(value):
        text = ""
    else:
        text = value if isinstance(value, str) else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        text = DEFAULT_USERINPUT_PLACEHOLDER
    return Markup("<br>\n").join(escape(line) for line in text.split("\n"))


def tojson(value) -> str:
    return json.dumps(value, indent=4, sort_keys=True, default=str)


def wrap(value, width: int = 80) -> str:
    return "\n".join(textwrap.wrap(value, width=width))


def pretty(value) -> str:
    return pprint.pformat(value, indent=4)


#: Filters that are safe to expose in the sandboxed mail environment.
SAFE_FILTERS = {
    "timezone": tz_convert,
    "isoformat": isoformat,
    "userinput": userinput,
    "htmlquote": htmlquote,
    "tojson": tojson,
    "textwrap": wrap,
}

#: Everything above plus filters that may leak repr() of arbitrary objects.
ALL_FILTERS = {**SAFE_FILTERS, "pprint": pretty}

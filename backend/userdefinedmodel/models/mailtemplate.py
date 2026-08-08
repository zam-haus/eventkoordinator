from django.db import models

from userdefinedmodel.basemodels import MetaBase


class MailTemplate(MetaBase):
    """A Jinja2 mail template, editable in UDM Admin → UDM Templating.

    Mirrors :class:`~userdefinedmodel.models.policy.Policy`: keyed by a stable
    human slug so it can be shipped in a UDM bundle and upserted on import.
    Rendered by ``userdefinedmodel.mailtemplates`` in a sandboxed Jinja2
    environment; ``example_input`` is the JSON used for the editor preview.
    """

    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True, default="")
    subject = models.TextField(blank=True, default="")
    body_text = models.TextField(blank=True, default="")
    body_html = models.TextField(blank=True, default="")
    example_input = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.slug

    class Meta:
        ordering = ["slug"]

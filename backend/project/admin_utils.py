"""
Reusable admin helpers for masking secret fields (API tokens, passwords).

The default Django admin renders ``CharField`` secrets in cleartext on the
change form and exposes them to anybody with admin read access. These helpers
mask the stored value on display while still allowing an admin to replace it
by typing a new value, and—crucially—preserve the existing secret when the
field is left blank on save (so editing another field does not wipe the
secret).

Usage::

    from project.admin_utils import MaskedSecretFormMixin, masked_secret_field

    class MyTargetAdmin(MaskedSecretFormMixin, PolymorphicChildModelAdmin):
        secret_fields = ("api_token",)
        fields = ("name", "api_token", ...)
"""
from __future__ import annotations

from typing import Sequence

from django import forms

# Placeholder shown in the input when a secret already exists. It is *not* a
# real value: the field's ``clean`` step discards it and reuses the stored
# secret when the user submits the form without typing anything new.
MASKED_PLACEHOLDER = "••••••••••••"


class MaskedSecretFormField(forms.CharField):
    """A ``CharField`` that masks an existing secret on display.

    * When the model already has a value, the widget is pre-filled with a
      fixed placeholder instead of the real secret.
    * On submit, an empty/placeholder value means "keep the existing secret";
      a non-empty value means "replace with this new secret".
    """

    # Render as a password-style input so the value is not shown in cleartext
    # while typing, and so browser password managers do not autocomplete it.
    widget = forms.PasswordInput(
        render_value=True,
        attrs={"placeholder": MASKED_PLACEHOLDER, "autocomplete": "new-password"},
    )

    def __init__(self, *args, model_instance=None, field_name: str = "", **kwargs):
        self.model_instance = model_instance
        self.field_name = field_name
        # Required only when there is no existing value to fall back on
        # (i.e. adding a new record). When editing a record that already has a
        # secret, the field is optional so a blank submission preserves it.
        existing = getattr(model_instance, field_name, "") or "" if model_instance else ""
        kwargs.setdefault("required", not existing)
        kwargs.setdefault("help_text", (
            "Leave blank to keep the current value. Enter a new value to replace it."
        ))
        super().__init__(*args, **kwargs)

    def clean(self, value):
        # Treat the placeholder and an empty string the same: "no change".
        if value in (None, "", MASKED_PLACEHOLDER):
            if self.model_instance is not None and self.field_name:
                existing = getattr(self.model_instance, self.field_name, "") or ""
                if existing:
                    # Preserve the stored secret on unchanged/blank submit.
                    return existing
            # No existing secret and nothing entered: defer to the base
            # field's required validation (raises "required" when the field
            # is required, e.g. on add; returns "" when blank=True).
            return super().clean("")
        return super().clean(value)


class MaskedSecretFormMixin:
    """ModelAdmin mixin that swaps declared secret fields for masked inputs.

    Set ``secret_fields`` on the ``ModelAdmin`` to the tuple of field names to
    mask. The mixin overrides ``get_form`` to replace each secret field's form
    field with a :class:`MaskedSecretFormField` bound to the current instance,
    so an unchanged/blank submission preserves the stored value.
    """

    secret_fields: Sequence[str] = ()

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        secret_fields = tuple(self.secret_fields)
        if not secret_fields:
            return form

        original_init = form.__init__

        def __init__(self, *args, **kwargs):
            instance = kwargs.get("instance")
            original_init(self, *args, **kwargs)
            # Replace the secret field on BOTH base_fields (for future form
            # instances) and self.fields (the per-instance copy used by this
            # form). self.fields is populated from base_fields during the
            # original __init__ above, so updating base_fields alone is not
            # enough.
            for field_name in secret_fields:
                if field_name in self.base_fields:
                    masked = MaskedSecretFormField(
                        model_instance=instance,
                        field_name=field_name,
                        label=self.base_fields[field_name].label,
                    )
                    self.base_fields[field_name] = masked
                    self.fields[field_name] = masked
                    # Never seed the widget with the real secret. If a value
                    # already exists, show the placeholder instead; otherwise
                    # leave blank. The clean() step restores the stored value
                    # when the user submits without typing anything.
                    existing = getattr(instance, field_name, "") or "" if instance else ""
                    self.initial[field_name] = MASKED_PLACEHOLDER if existing else ""

        form.__init__ = __init__
        return form


__all__ = ["MaskedSecretFormField", "MaskedSecretFormMixin", "MASKED_PLACEHOLDER"]

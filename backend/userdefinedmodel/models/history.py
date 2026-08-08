from django.db import models

from userdefinedmodel.basemodels import MetaBase


class EditGroup(MetaBase):
    node = models.ForeignKey(
        "userdefinedmodel.UserDefinedModelEntityNode",
        on_delete=models.CASCADE,
        related_name="edit_groups",
    )
    root_entity = models.ForeignKey(
        "userdefinedmodel.UserDefinedModelEntity",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="all_edit_groups",
    )
    saved_by = models.ForeignKey(
        "openid_user_management.OpenIDUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-saved_at"]

    def __str__(self):
        return f"EditGroup {self.id} by {self.saved_by}"


class FieldEdit(MetaBase):
    class ChangeKind(models.TextChoices):
        FIELD_VALUE = "field_value"
        NODE_ADDED = "node_added"
        NODE_REMOVED = "node_removed"
        NODE_REORDERED = "node_reordered"
        NODE_TRANSITION = "node_transition"
        POLICY_PRE_ACTION = "policy_pre_action"
        POLICY_POST_ACTION = "policy_post_action"

    group = models.ForeignKey(EditGroup, on_delete=models.CASCADE, related_name="field_edits")
    change_kind = models.CharField(
        max_length=20, choices=ChangeKind, default=ChangeKind.FIELD_VALUE
    )
    field = models.ForeignKey(
        "userdefinedmodel.DataField",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    language = models.CharField(max_length=10, blank=True, default="")
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    old_attachment = models.ForeignKey(
        "userdefinedmodel.FileAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="as_old_in_edits",
    )
    new_attachment = models.ForeignKey(
        "userdefinedmodel.FileAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="as_new_in_edits",
    )
    affected_node = models.ForeignKey(
        "userdefinedmodel.UserDefinedModelEntityNode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Preview parts identifying the affected (sub)model at the time of the edit:
    # [{"slug": ..., "text": ...}]. Stored per-slug so the history endpoint can
    # redact the parts whose field the viewer may not see.
    affected_node_summary = models.JSONField(default=list, blank=True)

    def save(self, *args, **kwargs):
        # Populated here rather than at each call site: history rows are written
        # from writer/engine/actions, and the summary must be the pre-write one,
        # which summaries.py snapshots at the start of the patch.
        if self._state.adding and self.affected_node_id and not self.affected_node_summary:
            from userdefinedmodel.summaries import summary_parts_for
            self.affected_node_summary = summary_parts_for(self.affected_node)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"FieldEdit {self.id} ({self.change_kind})"

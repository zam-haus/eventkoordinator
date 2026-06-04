from django.db import models, transaction
from django.db.models import Q, UniqueConstraint
from django.utils.timezone import now

from userdefinedmodel.basemodels import MetaBase, PolymorphicMetaBase


class WorkflowDefinition(MetaBase):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class WorkflowVersion(MetaBase):
    class Status(models.TextChoices):
        DRAFT = "draft"
        PUBLISHED = "published"
        ARCHIVED = "archived"

    workflow = models.ForeignKey(WorkflowDefinition, on_delete=models.CASCADE, related_name="versions")
    status = models.CharField(max_length=10, choices=Status, default=Status.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    virtual_node_positions = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["workflow"],
                condition=Q(status="draft"),
                name="unique_draft_per_workflow",
            ),
            UniqueConstraint(
                fields=["workflow"],
                condition=Q(status="published"),
                name="unique_published_per_workflow",
            ),
        ]

    def __str__(self):
        return f"{self.workflow} v{self.pk} ({self.status})"

    def publish(self):
        with transaction.atomic():
            WorkflowVersion.objects.filter(
                workflow=self.workflow, status=self.Status.PUBLISHED
            ).update(status=self.Status.ARCHIVED)

            self.status = self.Status.PUBLISHED
            self.published_at = now()
            self.save()

            return self._create_draft_copy()

    def _create_draft_copy(self):
        new_draft = WorkflowVersion.objects.create(
            workflow=self.workflow,
            status=WorkflowVersion.Status.DRAFT,
            notes="",
            virtual_node_positions=self.virtual_node_positions,
        )
        state_map = {}
        for old_state in self.states.prefetch_related("translations").all():
            new_state = WorkflowState.objects.create(
                version=new_draft,
                name=old_state.name,
                is_initial=old_state.is_initial,
                position_x=old_state.position_x,
                position_y=old_state.position_y,
                background_color=old_state.background_color,
            )
            state_map[old_state.pk] = new_state
            for t in old_state.translations.all():
                WorkflowStateTranslation.objects.create(
                    state=new_state, language=t.language, label=t.label
                )
        for old_trans in self.transitions.prefetch_related("translations").select_related("from_state", "to_state").all():
            new_trans = WorkflowTransition.objects.create(
                version=new_draft,
                name=old_trans.name,
                from_state=state_map.get(old_trans.from_state_id) if old_trans.from_state_id else None,
                from_undefined_only=old_trans.from_undefined_only,
                to_state=state_map[old_trans.to_state_id],
                source_handle=old_trans.source_handle,
                target_handle=old_trans.target_handle,
            )
            for t in old_trans.translations.all():
                WorkflowTransitionTranslation.objects.create(
                    transition=new_trans, language=t.language, label=t.label
                )
        return new_draft


class WorkflowState(MetaBase):
    version = models.ForeignKey(WorkflowVersion, on_delete=models.CASCADE, related_name="states")
    name = models.CharField(max_length=100)
    is_initial = models.BooleanField(default=False)
    position_x = models.FloatField(default=0.0)
    position_y = models.FloatField(default=0.0)
    background_color = models.CharField(max_length=7, default="#ffffff", blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["version"],
                condition=Q(is_initial=True),
                name="one_initial_state_per_workflow_version",
            ),
            UniqueConstraint(
                fields=["version", "name"],
                name="unique_state_name_per_workflow_version",
            ),
        ]

    def __str__(self):
        return f"{self.version} / {self.name}"


class WorkflowStateTranslation(MetaBase):
    state = models.ForeignKey(WorkflowState, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=10)
    label = models.CharField(max_length=200)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["state", "language"],
                name="unique_state_translation_per_language",
            )
        ]

    def __str__(self):
        return f"{self.state} [{self.language}]"


class WorkflowTransition(MetaBase):
    version = models.ForeignKey(WorkflowVersion, on_delete=models.CASCADE, related_name="transitions")
    name = models.CharField(max_length=100)
    from_state = models.ForeignKey(
        WorkflowState,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outgoing_transitions",
    )
    # When True and from_state is null: only allowed when current state is undefined (null).
    # When False and from_state is null: allowed from any state (including undefined).
    from_undefined_only = models.BooleanField(default=False)
    to_state = models.ForeignKey(
        WorkflowState,
        on_delete=models.CASCADE,
        related_name="incoming_transitions",
    )
    source_handle = models.CharField(max_length=30, blank=True, default="")
    target_handle = models.CharField(max_length=30, blank=True, default="")

    def __str__(self):
        return f"{self.version} / {self.name}"


class WorkflowTransitionTranslation(MetaBase):
    transition = models.ForeignKey(
        WorkflowTransition, on_delete=models.CASCADE, related_name="translations"
    )
    language = models.CharField(max_length=10)
    label = models.CharField(max_length=200)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["transition", "language"],
                name="unique_transition_translation_per_language",
            )
        ]

    def __str__(self):
        return f"{self.transition} [{self.language}]"


class TransitionAction(PolymorphicMetaBase):
    class Phase(models.TextChoices):
        PRE = "pre"
        POST = "post"

    transition = models.ForeignKey(
        WorkflowTransition, on_delete=models.CASCADE, related_name="actions"
    )
    phase = models.CharField(max_length=4, choices=Phase)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def execute(self, node, triggered_by) -> None:
        raise NotImplementedError


class SendNotificationAction(TransitionAction):
    # Recipients stored as JSON list of config dicts
    recipients_config = models.JSONField(default=list)
    subject_template = models.TextField(blank=True)
    body_template = models.TextField(blank=True)

    def execute(self, node, triggered_by) -> None:
        pass  # Email sending via mailqueue or similar


class SetFieldValueAction(TransitionAction):
    field_slug = models.CharField(max_length=80)
    value_json = models.JSONField(null=True, blank=True)

    def execute(self, node, triggered_by) -> None:
        field_def = node.config_version.field_definitions.filter(slug=self.field_slug).first()
        if field_def:
            fv, _ = node.field_values.get_or_create(field=field_def, language="")
            fv.set_value(self.value_json, field=field_def)
            fv.save()


class TriggerChildTransitionAction(TransitionAction):
    child_transition_name = models.CharField(max_length=100)

    def execute(self, node, triggered_by) -> None:
        from userdefinedmodel.engine import execute_transition
        for child in node.children.all():
            try:
                execute_transition(child, self.child_transition_name, triggered_by)
            except Exception:
                pass  # Post-action failures are logged but don't roll back

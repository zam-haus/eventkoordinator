from django.db import models, transaction
from django.db.models import Q, UniqueConstraint
from django.utils.timezone import now

from userdefinedmodel.basemodels import MetaBase


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
    # Free-form defaults merged into every transition descriptor of this
    # version (the transition's own properties win). Consumed by Rego policies
    # via input.candidate_transitions / input.transition_descriptor.
    properties = models.JSONField(default=dict, blank=True)

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
            properties=self.properties,
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
                properties=old_trans.properties,
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
    # Free-form JSON consumed by Rego policies (merged over the version's
    # properties) so rules can match on semantics instead of transition names.
    properties = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.version} / {self.name}"

    def to_descriptor(self) -> dict:
        """TransitionDescriptor for the policy input document (contract:
        documentation/configuration/policies/_input_schema.rego)."""
        return {
            "from_state": self.from_state.name if self.from_state else None,
            "to_state": self.to_state.name,
            "from_undefined_only": self.from_undefined_only,
            "properties": {**(self.version.properties or {}), **(self.properties or {})},
        }


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



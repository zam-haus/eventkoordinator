"""
Workflow versioning: introduce WorkflowVersion (draft/published/archived) as an
intermediary between WorkflowDefinition and its states/transitions, mirroring
FieldConfig → ConfigVersion.

Data migration steps:
  1. Create WorkflowVersion table.
  2. For each WorkflowDefinition, create a published WorkflowVersion and copy
     virtual_node_positions into it.
  3. Point WorkflowState.version at the new WorkflowVersion (replacing workflow FK).
  4. Point WorkflowTransition.version at the new WorkflowVersion (replacing workflow FK).
  5. Point FieldDefinition.workflow_version at the new WorkflowVersion (replacing
     workflow_definition FK).
  6. Remove old FK columns and virtual_node_positions from WorkflowDefinition.
"""
import uuid
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def migrate_workflow_versioning(apps, schema_editor):
    WorkflowDefinition = apps.get_model("userdefinedmodel", "WorkflowDefinition")
    WorkflowVersion = apps.get_model("userdefinedmodel", "WorkflowVersion")
    WorkflowState = apps.get_model("userdefinedmodel", "WorkflowState")
    WorkflowTransition = apps.get_model("userdefinedmodel", "WorkflowTransition")
    FieldDefinition = apps.get_model("userdefinedmodel", "FieldDefinition")

    for wf_def in WorkflowDefinition.objects.all():
        version = WorkflowVersion.objects.create(
            id=uuid.uuid4(),
            workflow=wf_def,
            status="published",
            published_at=django.utils.timezone.now(),
            notes="",
            virtual_node_positions=wf_def.virtual_node_positions or {},
        )
        WorkflowState.objects.filter(workflow=wf_def).update(version=version)
        WorkflowTransition.objects.filter(workflow=wf_def).update(version=version)
        FieldDefinition.objects.filter(workflow_definition=wf_def).update(workflow_version=version)


class Migration(migrations.Migration):

    dependencies = [
        ("userdefinedmodel", "0020_remove_udmtype_description"),
    ]

    operations = [
        # 1. Create WorkflowVersion model
        migrations.CreateModel(
            name="WorkflowVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(
                    choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")],
                    default="draft",
                    max_length=10,
                )),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("virtual_node_positions", models.JSONField(blank=True, default=dict)),
                ("workflow", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="versions",
                    to="userdefinedmodel.workflowdefinition",
                )),
            ],
            options={"abstract": False},
        ),
        migrations.AddConstraint(
            model_name="workflowversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="draft"),
                fields=["workflow"],
                name="unique_draft_per_workflow",
            ),
        ),
        migrations.AddConstraint(
            model_name="workflowversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="published"),
                fields=["workflow"],
                name="unique_published_per_workflow",
            ),
        ),

        # 2. Add version FK to WorkflowState (nullable first)
        migrations.AddField(
            model_name="workflowstate",
            name="version",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="states",
                to="userdefinedmodel.workflowversion",
            ),
        ),

        # 3. Add version FK to WorkflowTransition (nullable first)
        migrations.AddField(
            model_name="workflowtransition",
            name="version",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="transitions",
                to="userdefinedmodel.workflowversion",
            ),
        ),

        # 4. Add workflow_version FK to FieldDefinition (nullable first)
        migrations.AddField(
            model_name="fielddefinition",
            name="workflow_version",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="field_definitions",
                to="userdefinedmodel.workflowversion",
            ),
        ),

        # 5. Run data migration: create versions and repoint all FKs
        migrations.RunPython(migrate_workflow_versioning, migrations.RunPython.noop),

        # 6. Remove old unique constraints that reference workflow FK (now wrong scope)
        migrations.RemoveConstraint(
            model_name="workflowstate",
            name="one_initial_state_per_workflow",
        ),
        migrations.RemoveConstraint(
            model_name="workflowstate",
            name="unique_state_name_per_workflow",
        ),

        # 7. Remove old workflow FK from WorkflowState
        migrations.RemoveField(
            model_name="workflowstate",
            name="workflow",
        ),

        # 8. Remove old workflow FK from WorkflowTransition
        migrations.RemoveField(
            model_name="workflowtransition",
            name="workflow",
        ),

        # 9. Remove old workflow_definition FK from FieldDefinition
        migrations.RemoveField(
            model_name="fielddefinition",
            name="workflow_definition",
        ),

        # 10. Remove virtual_node_positions from WorkflowDefinition
        migrations.RemoveField(
            model_name="workflowdefinition",
            name="virtual_node_positions",
        ),

        # 11. Add version-scoped unique constraints to WorkflowState
        migrations.AddConstraint(
            model_name="workflowstate",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_initial=True),
                fields=["version"],
                name="one_initial_state_per_workflow_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="workflowstate",
            constraint=models.UniqueConstraint(
                fields=["version", "name"],
                name="unique_state_name_per_workflow_version",
            ),
        ),
    ]

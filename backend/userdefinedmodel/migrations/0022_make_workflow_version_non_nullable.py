"""
Make WorkflowState.version and WorkflowTransition.version non-nullable.
The data migration in 0021 has already filled every row, so no defaults needed.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("userdefinedmodel", "0021_workflow_versioning"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workflowstate",
            name="version",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="states",
                to="userdefinedmodel.workflowversion",
            ),
        ),
        migrations.AlterField(
            model_name="workflowtransition",
            name="version",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="transitions",
                to="userdefinedmodel.workflowversion",
            ),
        ),
    ]

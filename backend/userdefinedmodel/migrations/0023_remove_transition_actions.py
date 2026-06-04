from django.db import migrations


class Migration(migrations.Migration):
    """Drop the TransitionAction polymorphic model hierarchy.

    Actions are now declared by Rego policy output and dispatched via the
    handler registry in userdefinedmodel.actions — no DB rows needed.
    """

    dependencies = [
        ("userdefinedmodel", "0022_make_workflow_version_non_nullable"),
    ]

    operations = [
        migrations.DeleteModel(name="SendNotificationAction"),
        migrations.DeleteModel(name="SetFieldValueAction"),
        migrations.DeleteModel(name="TriggerChildTransitionAction"),
        migrations.DeleteModel(name="TransitionAction"),
    ]

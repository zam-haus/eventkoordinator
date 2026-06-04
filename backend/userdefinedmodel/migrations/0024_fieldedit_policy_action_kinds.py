from django.db import migrations, models


class Migration(migrations.Migration):
    """Add POLICY_PRE_ACTION and POLICY_POST_ACTION ChangeKind values.

    TextChoices are stored as VARCHAR; no data migration is required — the
    new values are simply added to the choices list.
    """

    dependencies = [
        ("userdefinedmodel", "0023_remove_transition_actions"),
    ]

    operations = [
        migrations.AlterField(
            model_name="fieldedit",
            name="change_kind",
            field=models.CharField(
                choices=[
                    ("field_value", "Field Value"),
                    ("node_added", "Node Added"),
                    ("node_removed", "Node Removed"),
                    ("node_reordered", "Node Reordered"),
                    ("node_transition", "Node Transition"),
                    ("policy_pre_action", "Policy Pre Action"),
                    ("policy_post_action", "Policy Post Action"),
                ],
                default="field_value",
                max_length=20,
            ),
        ),
    ]

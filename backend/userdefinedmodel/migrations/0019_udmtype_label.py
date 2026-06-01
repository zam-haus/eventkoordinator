from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("userdefinedmodel", "0018_add_hstack_group_tab_nav_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="userdefinedmodeltype",
            name="label",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]

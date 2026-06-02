from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("userdefinedmodel", "0019_udmtype_label"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="userdefinedmodeltype",
            name="description",
        ),
    ]

"""Drop the removed `overflow_data` column.

The field is gone from 0001_initial as well, so a fresh database never creates
the column and this migration is a no-op there. Databases migrated before the
removal still carry it, and the column is NOT NULL without a database-level
default, so it has to be dropped or every subsequent INSERT would fail.

`state_operations` is empty on purpose: the model state already lacks the field
(0001 no longer adds it), so this migration only touches the database.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("userdefinedmodel", "0029_mailtemplate"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE userdefinedmodel_userdefinedmodelentitynode "
                        "DROP COLUMN IF EXISTS overflow_data"
                    ),
                    # Irreversible by design: the values are gone with the column.
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[],
        ),
    ]

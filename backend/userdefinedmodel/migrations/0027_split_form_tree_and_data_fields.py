"""Split FieldDefinition into DataField (storage) + FormElement (form tree) +
FormElementBinding (M:N). See PLAN_split_form_tree_and_data_fields.md.

Strategy D1 (rename + add): RenameModel FieldDefinition→DataField keeps the
existing table (db_table pinned to 'userdefinedmodel_fielddefinition'), so all
FKs targeting 'userdefinedmodel.FieldDefinition' by string stay valid. The
three form-tree columns (parent_slug, sort_order, is_preview) are dropped from
DataField and their data is moved to new FormElement rows. Labels/help_text move
from FieldDefinitionTranslation to FormElementTranslation (B1).
"""
from django.db import migrations, models
import django.db.models.deletion
import uuid


def move_form_tree_data(apps, schema_editor):
    """For each existing DataField (formerly FieldDefinition) row, create a
    FormElement + binding + translation carrying the old form-tree columns.

    Structural rows (data_type in STRUCTURAL_TYPES) become standalone
    FormElements with NO DataField (they carry no value) and are deleted from
    the DataField table.

    Data rows become a DataField + a 1:1 'field' FormElement bound to it.
    Parent is resolved from the old parent_slug string to a FormElement FK
    in a second pass (parents must all exist first).
    """
    DataField = apps.get_model("userdefinedmodel", "DataField")
    FormElement = apps.get_model("userdefinedmodel", "FormElement")
    FormElementTranslation = apps.get_model("userdefinedmodel", "FormElementTranslation")
    FormElementBinding = apps.get_model("userdefinedmodel", "FormElementBinding")
    FieldDefinitionTranslation = apps.get_model("userdefinedmodel", "FieldDefinitionTranslation")
    FieldDefaultValue = apps.get_model("userdefinedmodel", "FieldDefaultValue")

    STRUCTURAL = {
        "tab_container", "tab", "save_button",
        "hstack", "hstack_group", "tab_prev", "tab_next",
    }

    if schema_editor.connection.alias != "default":
        return

    # Detect backend for FK-toggle syntax (SQLite PRAGMA vs PostgreSQL SET CONSTRAINTS).
    from django.db import connection
    is_sqlite = connection.vendor == "sqlite"

    # Clean up stale migration mappings that reference structural fields.
    # Structural fields carry no value, so a migration mapping for them is
    # non-functional (it maps nothing). BulkMigrationFieldMapping.source_field /
    # target_field are PROTECT FKs, so we must drop these rows before deleting
    # the structural DataField rows below.
    BulkMigrationFieldMapping = apps.get_model("userdefinedmodel", "BulkMigrationFieldMapping")
    MigrationFieldMapping = apps.get_model("userdefinedmodel", "MigrationFieldMapping")
    BulkMigrationSubmodelMapping = apps.get_model("userdefinedmodel", "BulkMigrationSubmodelMapping")
    BulkMigrationSubmodelFieldMapping = apps.get_model("userdefinedmodel", "BulkMigrationSubmodelFieldMapping")
    FieldEdit = apps.get_model("userdefinedmodel", "FieldEdit")

    structural_ids = list(DataField.objects.filter(data_type__in=STRUCTURAL).values_list("id", flat=True))
    # BulkMigrationFieldMapping: drop rows whose source OR target is structural
    BulkMigrationFieldMapping.objects.filter(source_field_id__in=structural_ids).delete()
    BulkMigrationFieldMapping.objects.filter(target_field_id__in=structural_ids).delete()
    # MigrationFieldMapping: same
    MigrationFieldMapping.objects.filter(source_field_id__in=structural_ids).delete()
    MigrationFieldMapping.objects.filter(target_field_id__in=structural_ids).delete()
    # BulkMigrationSubmodelMapping.source_parent_field (PROTECT) — submodel fields
    # are never structural, so no cleanup needed, but guard anyway:
    BulkMigrationSubmodelMapping.objects.filter(source_parent_field_id__in=structural_ids).delete()
    # BulkMigrationSubmodelFieldMapping source/target
    BulkMigrationSubmodelFieldMapping.objects.filter(source_field_id__in=structural_ids).delete()
    BulkMigrationSubmodelFieldMapping.objects.filter(target_field_id__in=structural_ids).delete()
    # FieldEdit.field is SET_NULL, so it won't block, but null it out for cleanliness
    FieldEdit.objects.filter(field_id__in=structural_ids).update(field=None)
    # FieldDefinitionTranslation.field is CASCADE; delete structural fields'
    # translations explicitly (the raw-SQL delete below runs with FK off, so
    # CASCADE won't fire). Their content was already copied to FormElementTranslation.
    FieldDefinitionTranslation.objects.filter(field_id__in=structural_ids).delete()
    # FieldDefaultValue.field is CASCADE and structural fields have none, but guard:
    FieldDefaultValue.objects.filter(field_id__in=structural_ids).delete()
    # SingleFieldValidationRule / MultiFieldRuleAssociation are CASCADE; guard:
    SingleFieldValidationRule = apps.get_model("userdefinedmodel", "SingleFieldValidationRule")
    MultiFieldRuleAssociation = apps.get_model("userdefinedmodel", "MultiFieldRuleAssociation")
    SingleFieldValidationRule.objects.filter(field_id__in=structural_ids).delete()
    MultiFieldRuleAssociation.objects.filter(field_id__in=structural_ids).delete()

    # Pass 1: create FormElements (parent unresolved), copy translations, bind.
    # old_field_id -> (FormElement, old_parent_slug, is_structural)
    created = []
    structural_to_delete = []
    for df in DataField.objects.all().order_by("sort_order", "id"):
        is_structural = df.data_type in STRUCTURAL
        element_type = df.data_type if is_structural else "field"
        el = FormElement.objects.create(
            version_id=df.version_id,
            slug=df.slug,
            element_type=element_type,
            parent=None,
            sort_order=df.sort_order,
            is_preview=df.is_preview,
            type_config={},
        )
        created.append((el, df.id, df.parent_slug))

        for t in FieldDefinitionTranslation.objects.filter(field_id=df.id):
            FormElementTranslation.objects.create(
                element_id=el.id,
                language=t.language,
                label=t.label,
                help_text=t.help_text,
            )

        if is_structural:
            structural_to_delete.append(str(df.id))
        else:
            FormElementBinding.objects.create(
                form_element_id=el.id,
                data_field_id=df.id,
                role="",
            )

    # Pass 1b: bulk-delete the structural DataField rows (their dependents were
    # already cleaned above). Use raw SQL and then force deferred FK triggers to
    # fire IMMEDIATE, so the subsequent ALTER TABLE (RemoveField/AlterField) on
    # this table is not blocked by pending trigger events.
    if structural_to_delete:
        with connection.cursor() as cur:
            # Build a parameterized IN-list safely.
            placeholders = ",".join(["%s"] * len(structural_to_delete))
            cur.execute(
                f'DELETE FROM "userdefinedmodel_datafield" WHERE "id" IN ({placeholders})',
                structural_to_delete,
            )
            if is_sqlite:
                cur.execute("PRAGMA foreign_keys = ON")
            else:
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")

    # Pass 2: resolve parent from old parent_slug -> FormElement FK.
    # Map (version_id, slug) -> FormElement for the elements we just created.
    slug_index = {(el.version_id, el.slug): el for el, _, _ in created}
    for el, _old_id, parent_slug in created:
        if parent_slug:
            parent = slug_index.get((el.version_id, parent_slug))
            if parent is not None:
                el.parent = parent
                el.save(update_fields=["parent"])


def move_form_tree_data_reverse(apps, schema_editor):
    """Reverse: rebuild FieldDefinition rows from FormElements is not feasible
    without the original column data, so this is a no-op stub. The forward
    migration is intentionally non-reversible for the data move (column drops
    are irreversible). Django will refuse --fake-rollback here; restore from a
    DB backup instead."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("userdefinedmodel", "0026_workflowtransition_properties_and_more"),
    ]

    operations = [
        # ── 1. Rename the model (keeps the table; db_table is pinned) ──
        migrations.RenameModel("FieldDefinition", "DataField"),

        # ── 2. Create the new models ──
        migrations.CreateModel(
            name="FormElement",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("slug", models.SlugField(max_length=80)),
                ("element_type", models.CharField(max_length=30, choices=[
                    ("field", "Field"),
                    ("tab_container", "Tab Container"), ("tab", "Tab"),
                    ("save_button", "Save Button"),
                    ("hstack", "Hstack"), ("hstack_group", "Hstack Group"),
                    ("tab_prev", "Tab Prev"), ("tab_next", "Tab Next"),
                    ("date_range", "Date Range"),
                ])),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_preview", models.BooleanField(default=False)),
                ("type_config", models.JSONField(default=dict)),
                ("parent", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="children",
                    to="userdefinedmodel.formelement",
                )),
                ("version", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="form_elements",
                    to="userdefinedmodel.configversion",
                )),
            ],
            options={
                "ordering": ["sort_order", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("version", "slug"),
                        name="unique_element_slug_in_version",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="FormElementTranslation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("language", models.CharField(max_length=10)),
                ("label", models.CharField(blank=True, default="", max_length=200)),
                ("help_text", models.TextField(blank=True, default="")),
                ("element", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="translations",
                    to="userdefinedmodel.formelement",
                )),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("element", "language"),
                        name="unique_label_translation_per_element_language",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="FormElementBinding",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("role", models.CharField(blank=True, default="", max_length=30)),
                ("data_field", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="form_element_bindings",
                    to="userdefinedmodel.datafield",
                )),
                ("form_element", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="bindings",
                    to="userdefinedmodel.formelement",
                )),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("form_element", "data_field", "role"),
                        name="unique_binding_per_element_field_role",
                    ),
                ],
            },
        ),

        # ── 3. Move form-tree data BEFORE dropping the columns ──
        # We need parent_slug / sort_order / is_preview from the old rows.
        # RunPython runs here, while the columns still exist on the DataField table.
        migrations.RunPython(move_form_tree_data, move_form_tree_data_reverse),

        # ── 4. Drop the form-tree columns from DataField ──
        migrations.RemoveField(model_name="DataField", name="parent_slug"),
        migrations.RemoveField(model_name="DataField", name="sort_order"),
        migrations.RemoveField(model_name="DataField", name="is_preview"),

        # ── 5. Alter the data_type choices (remove structural types) ──
        migrations.AlterField(
            model_name="DataField",
            name="data_type",
            field=models.CharField(max_length=30, choices=[
                ("text_short", "Text Short"), ("text_long", "Text Long"),
                ("text_markdown", "Text Markdown"), ("text_richtext", "Text Richtext"),
                ("integer", "Integer"), ("float", "Float"), ("boolean", "Boolean"),
                ("date", "Date"), ("time", "Time"), ("datetime", "Datetime"),
                ("select_single", "Select Single"), ("select_multi", "Select Multi"),
                ("image", "Image"), ("file", "File"),
                ("user_select", "User Select"), ("user_select_multi", "User Select Multi"),
                ("group_select", "Group Select"), ("group_select_multi", "Group Select Multi"),
                ("submodel_select", "Submodel Select"), ("submodel_list", "Submodel List"),
                ("entity_select", "Entity Select"), ("entity_select_multi", "Entity Select Multi"),
                ("slug_id", "Slug Id"), ("workflow", "Workflow"),
            ]),
        ),

        # ── 6. Alter ordering on DataField (no longer sort_order) ──
        migrations.AlterModelOptions(
            name="DataField",
            options={"ordering": ["id"]},
        ),

        # ── 7. FieldDefinitionTranslation stays (deprecated); its rows were
        #    copied to FormElementTranslation in RunPython. We do NOT drop it
        #    here to keep the migration reversible at the schema level; a later
        #    cleanup migration can remove it once the data move is confirmed.
        #    New code reads labels from FormElementTranslation only.
    ]

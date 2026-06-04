"""
Celery tasks for userdefinedmodel.
"""
import logging

from celery import shared_task
from django.db import transaction
from django.db.utils import OperationalError
from django.utils.timezone import now

logger = logging.getLogger(__name__)


def run_bulk_migration(plan_id: str) -> None:
    """Execute a BulkMigrationPlan synchronously.

    Called by the Celery task wrapper below and directly by management commands
    that need synchronous execution without going through a broker.
    """
    from userdefinedmodel.models import (
        BulkMigrationPlan, UserDefinedModelEntity,
        UserDefinedModelEntityMigration, MigrationFieldMapping, FieldValue,
    )

    try:
        with transaction.atomic():
            try:
                plan = BulkMigrationPlan.objects.select_for_update(nowait=True).get(id=plan_id)
            except BulkMigrationPlan.DoesNotExist:
                logger.error("BulkMigrationPlan %s not found", plan_id)
                return
            except OperationalError:
                logger.warning("BulkMigrationPlan %s already running", plan_id)
                return

            if plan.status == BulkMigrationPlan.Status.RUNNING:
                logger.warning("BulkMigrationPlan %s already running", plan_id)
                return

            qs = UserDefinedModelEntity.objects.filter(config_version=plan.source_version)
            if plan.user_defined_model_type_filter:
                qs = qs.filter(user_defined_model_type=plan.user_defined_model_type_filter)

            plan.status = BulkMigrationPlan.Status.RUNNING
            plan.total_entities = qs.count()
            plan.save(update_fields=["status", "total_entities"])

        entity_ids = list(
            UserDefinedModelEntity.objects.filter(config_version=plan.source_version)
            .filter(**({} if not plan.user_defined_model_type_filter else {"user_defined_model_type": plan.user_defined_model_type_filter}))
            .values_list("id", flat=True)
        )

        mappings = list(plan.field_mappings.select_related("source_field", "target_field").all())
        tgt_version = plan.target_version

        # Build workflow state override lookup: {field_slug: {from_state: to_state}}
        workflow_state_overrides: dict[str, dict[str, str]] = {}
        for wsm in plan.workflow_state_mappings.all():
            workflow_state_overrides.setdefault(wsm.field_slug, {})[wsm.from_state] = wsm.to_state

        # Load submodel mappings with their child field mappings
        submodel_mappings = list(
            plan.submodel_mappings
            .select_related("source_parent_field__submodel_config__config", "target_submodel_version")
            .prefetch_related("field_mappings__source_field", "field_mappings__target_field")
            .all()
        )

        # Pre-build target parent field lookup by slug so child parent_field is
        # updated to the new version's field after migration.
        tgt_parent_fields_by_slug = {
            fd.slug: fd for fd in tgt_version.field_definitions.all()
        }

        for entity_id in entity_ids:
            try:
                with transaction.atomic():
                    try:
                        entity = (UserDefinedModelEntity.objects
                                  .select_for_update(nowait=True, of=("self",))
                                  .select_related("config_version", "user_defined_model_type")
                                  .get(id=entity_id))
                    except OperationalError:
                        _increment_failed(plan)
                        continue

                    migration = UserDefinedModelEntityMigration.objects.create(
                        user_defined_model_entity=entity,
                        source_version=entity.config_version,
                        target_user_defined_model_type=plan.user_defined_model_type_filter or entity.user_defined_model_type,
                        target_version=tgt_version,
                        bulk_plan=plan,
                        executed_by=None,
                    )

                    _apply_field_mappings_to_node(
                        entity, mappings, tgt_version, workflow_state_overrides,
                        audit_migration=migration,
                    )

                    # Migrate child nodes for each submodel mapping.
                    # Match by slug (not FK) so orphaned children whose parent_field
                    # still points to an older source version are also migrated.
                    for sm in submodel_mappings:
                        slug = sm.source_parent_field.slug
                        target_parent_field = tgt_parent_fields_by_slug.get(slug)
                        sm_config = sm.source_parent_field.submodel_config
                        if sm_config is None:
                            continue
                        children = list(
                            entity.children
                            .filter(parent_field__slug=slug, config_version__config=sm_config.config)
                            .exclude(config_version=sm.target_submodel_version)
                        )
                        child_field_mappings = list(sm.field_mappings.all())
                        for child in children:
                            _apply_field_mappings_to_node(
                                child, child_field_mappings, sm.target_submodel_version, {},
                            )
                            if target_parent_field:
                                child.parent_field = target_parent_field
                                child.save(update_fields=["parent_field"])

                    migration.executed_at = now()
                    migration.save(update_fields=["executed_at"])

                with transaction.atomic():
                    from django.db.models import F
                    BulkMigrationPlan.objects.filter(id=plan_id).update(done_entities=F("done_entities") + 1)

            except Exception as exc:
                from django.core.exceptions import ValidationError
                if isinstance(exc, ValidationError):
                    errs = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
                    logger.warning("Entity %s skipped: validation failed after migration: %s", entity_id, errs)
                else:
                    logger.exception("Entity %s migration failed: %s", entity_id, exc)
                _increment_failed(plan)

        plan.refresh_from_db()
        final_status = BulkMigrationPlan.Status.DONE if plan.failed_entities == 0 else BulkMigrationPlan.Status.PARTIAL
        BulkMigrationPlan.objects.filter(id=plan_id).update(status=final_status, executed_at=now())

    except Exception as exc:
        logger.exception("run_bulk_migration failed: %s", exc)
        BulkMigrationPlan.objects.filter(id=plan_id).update(status=BulkMigrationPlan.Status.PARTIAL)


@shared_task(bind=True, max_retries=0)
def execute_bulk_migration(self, plan_id: str):
    run_bulk_migration(plan_id)


def _apply_field_mappings_to_node(node, mappings, target_version, workflow_state_overrides, *, audit_migration=None):
    """Apply a list of field mappings to a node, update its config_version, validate, and materialize defaults.

    mappings: iterable of objects with .source_field, .action, .target_field attributes.
    workflow_state_overrides: {field_slug: {from_state: to_state}} for workflow fields.
    audit_migration: if set, creates MigrationFieldMapping audit rows on it.
    """
    from userdefinedmodel.models import FieldValue, MigrationFieldMapping

    old_version = node.config_version
    overflow = {}
    for bm in mappings:
        src_field = bm.source_field
        fv = node.field_values.filter(field=src_field).first()
        if fv is None:
            continue

        if audit_migration:
            MigrationFieldMapping.objects.create(
                migration=audit_migration, source_field=src_field,
                action=bm.action, target_field=bm.target_field,
            )

        if bm.action == "map" and bm.target_field:
            overrides = workflow_state_overrides.get(src_field.slug, {}) if workflow_state_overrides else {}
            val = _resolve_migration_value(fv, bm.target_field, state_overrides=overrides)
            if val is not None:
                new_fv, _ = FieldValue.objects.get_or_create(
                    node=node, field=bm.target_field, language=fv.language
                )
                new_fv.set_value(val, field=bm.target_field)
                new_fv.save()
        elif bm.action == "overflow":
            overflow[src_field.slug] = str(fv.get_value())

    if overflow:
        node.overflow_data = {**node.overflow_data, **overflow}

    # Remove stale field values from the old version so they don't pollute
    # serialization or conflict with values created by materialize_defaults.
    node.field_values.filter(field__version=old_version).delete()

    node.config_version = target_version
    node.validate_for_save()
    node.save(update_fields=["config_version", "overflow_data"])
    node.materialize_defaults()


def _resolve_migration_value(src_fv, tgt_field, *, state_overrides=None):
    """Return a value suitable for set_value(val, field=tgt_field), or None to skip.

    state_overrides: {from_state_name: to_state_name} for workflow field remapping.
    """
    from userdefinedmodel.models.config import FieldDefinition
    val = src_fv.get_value()
    if val is None:
        return None
    if tgt_field.data_type == FieldDefinition.DataType.WORKFLOW:
        if not tgt_field.workflow_version_id or not isinstance(val, str):
            return None
        target_state_name = val
        if state_overrides:
            target_state_name = state_overrides.get(val, val)
        from userdefinedmodel.models import WorkflowState
        state = WorkflowState.objects.filter(
            version_id=tgt_field.workflow_version_id, name=target_state_name
        ).first()
        return state
    return val


def _increment_failed(plan):
    from userdefinedmodel.models import BulkMigrationPlan
    from django.db.models import F
    BulkMigrationPlan.objects.filter(id=plan.id).update(failed_entities=F("failed_entities") + 1)

"""Bundle export/import + bulk migration routes:
/export-bundle-zip/, /parse-bundle-zip/, /import-bundle-zip/, /bulk-migrations/...
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from django.db import transaction
from django.http import JsonResponse
from ninja import File, Router, UploadedFile
from ninja.security import django_auth

from userdefinedmodel.api_helpers import (
    ApiError,
    _create_field_default,
    _require_perms,
    _serialize_version_as_draft_in,
    _wcag_text_color,
)
from userdefinedmodel.schemas import (
    BulkMigrationCreateIn,
    BulkMigrationOut,
    BulkMigrationStatus,
    BundleExportIn,
    BundleExportOut,
    BundleFieldConfigOut,
    BundleUDMTypeOut,
    BundleWorkflowOut,
    ConfigLanguageOut,
    PolicyOut,
    WorkflowStateOut,
    WorkflowTransitionOut,
)

router = Router(auth=django_auth)


# ─── Bundle export / import ───────────────────────────────────────────────────

_BUNDLE_RULE = "UDM_BUNDLE"


def _extract_bundle_from_rego(source: str) -> dict | None:
    """Evaluate the UDM_BUNDLE rule in a Rego source string and return the bundle dict.

    The bundle must be defined as a top-level rule in the udm package:
        UDM_BUNDLE := { ... }
    It is evaluated with an empty input document, so it may reference other rules
    and Rego built-ins but not input.* fields.
    Returns None if the rule is missing or evaluation fails.
    """
    import json as _json
    try:
        import regorus
        eng = regorus.Engine()
        eng.add_policy("bundle.rego", source)
        eng.set_input_json("{}")
        raw = _json.loads(eng.eval_rule_as_json(f"data.udm.{_BUNDLE_RULE}"))
        if raw == "<undefined>" or raw is None:
            return None
        if isinstance(raw, list) and raw:
            raw = raw[0]
        if isinstance(raw, dict):
            return raw
        return None
    except Exception:
        return None


def _collect_bundle_scope(scope_type_ids: list) -> tuple[list, list, list, list]:
    """Collect all UDMTypes, field configs, workflows, and policies for the given UDMType IDs.

    Returns (udm_types, field_configs, workflows, policies) — all DB model objects.
    Field configs include both root configs and any submodel configs reachable from them.
    """
    from userdefinedmodel.models import (
        UserDefinedModelType, FieldConfig, ConfigVersion, WorkflowDefinition, Policy,
    )

    udm_types = list(
        UserDefinedModelType.objects.select_related("field_config")
        .prefetch_related("type_policies__policy")
        .filter(id__in=scope_type_ids)
    )

    # Collect root field configs from UDMTypes
    config_ids: set = set()
    for t in udm_types:
        if t.field_config_id:
            config_ids.add(t.field_config_id)

    # Walk submodel configs transitively
    visited_version_ids: set = set()
    workflow_def_ids: set = set()
    configs_to_expand = list(config_ids)
    while configs_to_expand:
        cfg_id = configs_to_expand.pop()
        # Use the published version if available, else draft
        try:
            version = ConfigVersion.objects.prefetch_related(
                "field_definitions__workflow_version__workflow",
            ).get(config_id=cfg_id, status=ConfigVersion.Status.PUBLISHED)
        except ConfigVersion.DoesNotExist:
            try:
                version = ConfigVersion.objects.prefetch_related(
                    "field_definitions__workflow_version__workflow",
                ).get(config_id=cfg_id, status=ConfigVersion.Status.DRAFT)
            except ConfigVersion.DoesNotExist:
                continue
        if version.id in visited_version_ids:
            continue
        visited_version_ids.add(version.id)
        for fd in version.field_definitions.all():
            if fd.workflow_version_id and fd.workflow_version.workflow_id:
                workflow_def_ids.add(fd.workflow_version.workflow_id)
            if fd.submodel_config_id and fd.submodel_config.config_id not in config_ids:
                config_ids.add(fd.submodel_config.config_id)
                configs_to_expand.append(fd.submodel_config.config_id)

    field_configs = list(FieldConfig.objects.prefetch_related("languages").filter(id__in=config_ids))
    workflows = list(
        WorkflowDefinition.objects.prefetch_related(
            "versions__states__translations",
            "versions__transitions__translations",
            "versions__transitions__from_state",
            "versions__transitions__to_state",
        ).filter(id__in=workflow_def_ids)
    )

    policy_slugs: set[str] = set()
    for t in udm_types:
        for tp in t.type_policies.all():
            policy_slugs.add(tp.policy.slug)
    policies = list(Policy.objects.filter(slug__in=policy_slugs))

    return udm_types, field_configs, workflows, policies


def _build_bundle_export(scope_type_ids: list) -> BundleExportOut:
    """Build a BundleExportOut for the given scope UDMType IDs."""
    from userdefinedmodel.models import ConfigVersion

    udm_types, field_configs, workflows, policies = _collect_bundle_scope(scope_type_ids)

    bundle_udm_types = []
    for t in udm_types:
        bundle_udm_types.append(BundleUDMTypeOut(
            id=t.id,
            name=t.name,
            field_config_id=t.field_config_id,
            policy_slugs=[tp.policy.slug for tp in t.type_policies.all()],
        ))

    bundle_cfg_ids = {str(cfg.id) for cfg in field_configs}
    bundle_field_configs = []
    for cfg in field_configs:
        try:
            version = ConfigVersion.objects.get(config=cfg, status=ConfigVersion.Status.PUBLISHED)
        except ConfigVersion.DoesNotExist:
            try:
                version = ConfigVersion.objects.get(config=cfg, status=ConfigVersion.Status.DRAFT)
            except ConfigVersion.DoesNotExist:
                continue
        draft_export = _serialize_version_as_draft_in(version, bundle_config_ids=bundle_cfg_ids)
        bundle_field_configs.append(BundleFieldConfigOut(
            id=cfg.id,
            name=cfg.name,
            description=cfg.description,
            languages=[
                ConfigLanguageOut(code=l.code, label=l.label, is_default=l.is_default, sort_order=l.sort_order)
                for l in cfg.languages.all()
            ],
            draft=draft_export,
        ))

    bundle_workflows = []
    for wf_def in workflows:
        # Export the published version, falling back to draft
        export_version = (
            next((v for v in wf_def.versions.all() if v.status == "published"), None)
            or next((v for v in wf_def.versions.all() if v.status == "draft"), None)
        )
        if not export_version:
            continue
        bundle_workflows.append(BundleWorkflowOut(
            id=wf_def.id,
            name=wf_def.name,
            description=wf_def.description,
            states=[
                WorkflowStateOut(
                    name=s.name,
                    label={t.language: t.label for t in s.translations.all()},
                    is_initial=s.is_initial,
                    position_x=s.position_x,
                    position_y=s.position_y,
                    background_color=s.background_color or "#ffffff",
                    text_color=_wcag_text_color(s.background_color or "#ffffff"),
                )
                for s in export_version.states.all()
            ],
            transitions=[
                WorkflowTransitionOut(
                    name=tr.name,
                    label={t.language: t.label for t in tr.translations.all()},
                    from_state=tr.from_state.name if tr.from_state else None,
                    from_undefined_only=tr.from_undefined_only,
                    to_state=tr.to_state.name,
                    source_handle=tr.source_handle,
                    target_handle=tr.target_handle,
                )
                for tr in export_version.transitions.all()
            ],
            virtual_node_positions=export_version.virtual_node_positions or {},
        ))

    bundle_policies = [PolicyOut(slug=p.slug, source=p.source) for p in policies]

    return BundleExportOut(
        version=1,
        scope_type_ids=[t.id for t in udm_types],
        udm_types=bundle_udm_types,
        field_configs=bundle_field_configs,
        workflows=bundle_workflows,
        policies=bundle_policies,
    )


def _build_bundle_zip(scope_type_ids: list) -> bytes:
    """Build a ZIP archive containing:
    - UDM_BUNDLE.json  — structural bundle without policy sources
    - policies/<slug>.rego — one file per policy
    Returns raw ZIP bytes.
    """
    import io
    import zipfile as _zf
    bundle = _build_bundle_export(scope_type_ids)
    # Strip policy sources from the embedded bundle — they live as separate files
    bundle_dict = bundle.model_dump(mode="json")
    for p in bundle_dict.get("policies", []):
        p.pop("source", None)
    bundle_json = json.dumps(bundle_dict, indent=2, ensure_ascii=False)

    buf = io.BytesIO()
    with _zf.ZipFile(buf, "w", compression=_zf.ZIP_DEFLATED) as zf:
        zf.writestr("UDM_BUNDLE.json", bundle_json)
        for policy in bundle.policies:
            zf.writestr(f"policies/{policy.slug}.rego", policy.source)
    return buf.getvalue()


def _extract_bundle_from_zip(zip_bytes: bytes) -> tuple[dict | None, dict[str, str]]:
    """Parse a ZIP bundle archive.

    Returns (bundle_dict, policy_sources) where:
    - bundle_dict is from UDM_BUNDLE.json or evaluated UDM_BUNDLE.rego (None on failure)
    - policy_sources maps slug → source from policies/*.rego files
    """
    import io
    import zipfile as _zf
    policy_sources: dict[str, str] = {}
    bundle_dict: dict | None = None

    try:
        with _zf.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            # Read policy files
            for name in names:
                if name.startswith("policies/") and name.endswith(".rego"):
                    slug = name[len("policies/"):-len(".rego")]
                    if slug:
                        policy_sources[slug] = zf.read(name).decode("utf-8")
            # Prefer UDM_BUNDLE.json, fall back to UDM_BUNDLE.rego
            if "UDM_BUNDLE.json" in names:
                bundle_dict = json.loads(zf.read("UDM_BUNDLE.json").decode("utf-8"))
            elif "UDM_BUNDLE.rego" in names:
                rego_src = zf.read("UDM_BUNDLE.rego").decode("utf-8")
                bundle_dict = _extract_bundle_from_rego(rego_src)
    except Exception:
        pass
    return bundle_dict, policy_sources


@router.post("/export-bundle-zip/", auth=django_auth)
def export_bundle_zip(request, payload: BundleExportIn):
    """Export a ZIP bundle: UDM_BUNDLE.json + policies/<slug>.rego for each policy."""
    from django.http import HttpResponse
    if denied := _require_perms(request, "userdefinedmodel.view_fieldconfig", "userdefinedmodel.view_datafield"):
        return denied
    zip_bytes = _build_bundle_zip(payload.scope_type_ids)
    response = HttpResponse(zip_bytes, content_type="application/zip")
    response["Content-Disposition"] = "attachment; filename=\"udm_bundle.zip\""
    return response


@router.post("/parse-bundle-zip/", auth=django_auth)
def parse_bundle_zip(request, file: UploadedFile = File(...)):
    """Parse a ZIP bundle and return the scope_type_ids and udm_types metadata it declares."""
    bundle_dict, _ = _extract_bundle_from_zip(file.read())
    if bundle_dict is None:
        return JsonResponse({"scope_type_ids": [], "udm_types": [], "error": "Could not parse UDM_BUNDLE from ZIP"})
    udm_types = [
        {"id": str(t["id"]), "name": t.get("name", ""), "description": t.get("description", "")}
        for t in bundle_dict.get("udm_types", [])
    ]
    return JsonResponse({
        "scope_type_ids": bundle_dict.get("scope_type_ids", []),
        "udm_types": udm_types,
    })


@router.post("/import-bundle-zip/", auth=django_auth)
def import_bundle_zip(
    request,
    file: UploadedFile = File(...),
    scope_type_ids: str = "",
    policy_slug: str = "",
):
    """Import a ZIP bundle (UDM_BUNDLE.json + policies/*.rego).

    scope_type_ids: comma-separated UUID strings of in-scope UDM Types.
    policy_slug: if set, save each policy rego with its own slug (already done from policies/ dir).
    """
    from userdefinedmodel.models import (
        ConfigVersion, FieldConfig, ConfigLanguage, FieldDefinition,
        FieldDefinitionTranslation, WorkflowDefinition,
        Policy, UserDefinedModelType, UserDefinedModelTypePolicy,
    )
    if denied := _require_perms(
        request,
        "userdefinedmodel.change_fieldconfig",
        "userdefinedmodel.change_datafield",
        "userdefinedmodel.change_policy",
    ):
        return denied

    zip_bytes = file.read()
    raw_bundle, zip_policy_sources = _extract_bundle_from_zip(zip_bytes)
    if raw_bundle is None:
        return JsonResponse({"detail": "Could not parse UDM_BUNDLE from ZIP"}, status=400)

    # Parse scope_type_ids from query param (comma-separated)
    parsed_scope_ids = set(s.strip() for s in scope_type_ids.split(",") if s.strip())
    if not parsed_scope_ids:
        # Fall back to the bundle's own scope
        parsed_scope_ids = set(str(s) for s in raw_bundle.get("scope_type_ids", []))
    if not parsed_scope_ids:
        return JsonResponse({"detail": "scope_type_ids is required"}, status=400)

    bundle_config_ids = set(str(fc["id"]) for fc in raw_bundle.get("field_configs", []))

    with transaction.atomic():
        # ── Step 1: Resolve workflows ─────────────────────────────────────────
        # workflow_id_map maps WorkflowDefinition.id (from bundle) → WorkflowVersion (published)
        workflow_id_map: dict[str, object] = {}
        for wf_data in raw_bundle.get("workflows", []):
            wf_id = str(wf_data["id"])
            try:
                wf = WorkflowDefinition.objects.prefetch_related(
                    "versions__states__translations",
                    "versions__transitions__translations",
                    "versions__transitions__from_state",
                    "versions__transitions__to_state",
                ).get(id=wf_id)
            except WorkflowDefinition.DoesNotExist:
                wf = None

            if wf is not None and _is_workflow_externally_used(wf.id, bundle_config_ids):
                workflow_id_map[wf_id] = _clone_workflow(wf)
            elif wf is not None:
                workflow_id_map[wf_id] = _update_workflow_from_data(wf, wf_data)
            else:
                workflow_id_map[wf_id] = _create_workflow_from_data(wf_data)

        # ── Step 2: Resolve field configs ─────────────────────────────────────
        fc_by_id = {str(fc["id"]): fc for fc in raw_bundle.get("field_configs", [])}
        ordered_config_ids = _toposort_configs(fc_by_id)
        config_id_map: dict[str, object] = {}
        # Track (draft, cfg_id) in topo order so we can publish leaf-first after all are created.
        # pending_submodel_refs collects (FieldDefinition, cfg_id) pairs where the bundle used a
        # FieldConfig UUID as the submodel reference — resolved to a published version after publish.
        drafts_to_publish: list = []
        pending_submodel_refs: list = []

        for cfg_id in ordered_config_ids:
            fc_data = fc_by_id[cfg_id]
            try:
                cfg = FieldConfig.objects.prefetch_related("languages").get(id=cfg_id)
                cfg_exists = True
            except FieldConfig.DoesNotExist:
                cfg = None
                cfg_exists = False

            if cfg_exists and _is_config_externally_used(cfg.id, parsed_scope_ids, bundle_config_ids):
                new_cfg, new_draft = _clone_field_config(cfg, fc_data.get("languages", []))
                config_id_map[cfg_id] = new_cfg
                _apply_draft_fields(new_draft, fc_data["draft"], workflow_id_map, config_id_map, bundle_config_ids, pending_submodel_refs)
                drafts_to_publish.append(new_draft)
            elif cfg_exists:
                cfg.name = fc_data["name"]
                cfg.description = fc_data.get("description", "")
                cfg.save()
                cfg.languages.all().delete()
                for lang in fc_data.get("languages", []):
                    ConfigLanguage.objects.create(
                        config=cfg, code=lang["code"], label=lang["label"],
                        is_default=lang["is_default"], sort_order=lang["sort_order"],
                    )
                draft, _ = ConfigVersion.objects.get_or_create(
                    config=cfg, status=ConfigVersion.Status.DRAFT,
                    defaults={"notes": fc_data["draft"].get("notes", "")},
                )
                draft.notes = fc_data["draft"].get("notes", "")
                draft.save(update_fields=["notes"])
                _apply_draft_fields(draft, fc_data["draft"], workflow_id_map, config_id_map, bundle_config_ids, pending_submodel_refs)
                config_id_map[cfg_id] = cfg
                drafts_to_publish.append(draft)
            else:
                new_cfg, new_draft = _clone_field_config(
                    type("FakeConfig", (), {"name": fc_data["name"], "description": fc_data.get("description", "")})(),
                    fc_data.get("languages", []),
                    id=cfg_id,
                )
                config_id_map[cfg_id] = new_cfg
                _apply_draft_fields(new_draft, fc_data["draft"], workflow_id_map, config_id_map, bundle_config_ids, pending_submodel_refs)
                drafts_to_publish.append(new_draft)

        # Publish drafts leaf-first (topo order: submodels before their parents).
        # After each draft publishes, resolve any pending submodel_config refs
        # that point to it, so parent configs that reference it have a non-null
        # submodel_config by the time they publish (publish() enforces that
        # submodel fields have a config).
        published_cfg_to_version: dict = {}
        for draft in drafts_to_publish:
            draft.publish()
            published_cfg_to_version[str(draft.config_id)] = draft
            # Resolve pending refs pointing to this freshly-published config.
            still_pending = []
            for fd, cfg_id in pending_submodel_refs:
                if cfg_id == str(draft.config_id):
                    try:
                        fd.submodel_config = draft
                        fd.save(update_fields=["submodel_config"])
                    except Exception:
                        pass
                else:
                    still_pending.append((fd, cfg_id))
            pending_submodel_refs[:] = still_pending

        # Resolve any remaining submodel_config references (e.g. pointing to
        # configs published earlier in a previous import).
        for fd, cfg_id in pending_submodel_refs:
            sub_cfg = config_id_map.get(cfg_id)
            if not sub_cfg:
                continue
            try:
                published_version = ConfigVersion.objects.get(
                    config=sub_cfg, status=ConfigVersion.Status.PUBLISHED
                )
                fd.submodel_config = published_version
                fd.save(update_fields=["submodel_config"])
            except ConfigVersion.DoesNotExist:
                pass

        # ── Step 3: Policies from ZIP files ───────────────────────────────────
        policy_slug_map: dict[str, object] = {}
        for pol_data in raw_bundle.get("policies", []):
            slug = pol_data.get("slug", "")
            if not slug:
                continue
            source = zip_policy_sources.get(slug) or pol_data.get("source", "")
            if source:
                pol, _ = Policy.objects.update_or_create(slug=slug, defaults={"source": source})
            else:
                pol, _ = Policy.objects.get_or_create(slug=slug, defaults={"source": ""})
            policy_slug_map[slug] = pol


        # ── Step 4: UDMTypes — create if missing, always relink ───────────────
        # Maps bundle cfg_id → the first config assigned to a bundle UDMType,
        # used for linking fallback scope types.
        bundle_type_cfg_map: dict[str, object] = {}  # bundle_type_id → FieldConfig
        bundle_type_policy_slugs: list[str] = []

        for udmt_data in raw_bundle.get("udm_types", []):
            udmt_id = str(udmt_data["id"])
            try:
                udmt = UserDefinedModelType.objects.get(id=udmt_id)
            except UserDefinedModelType.DoesNotExist:
                # Create the type, preserving its UUID so round-trip imports work
                udmt = UserDefinedModelType.objects.create(
                    id=udmt_id,
                    name=udmt_data["name"],
                )
            old_cfg_id = str(udmt_data.get("field_config_id") or "")
            if old_cfg_id in config_id_map:
                udmt.field_config = config_id_map[old_cfg_id]
                udmt.save(update_fields=["field_config"])
                bundle_type_cfg_map[udmt_id] = config_id_map[old_cfg_id]
            for p_slug in udmt_data.get("policy_slugs", []):
                if p_slug in policy_slug_map:
                    UserDefinedModelTypePolicy.objects.get_or_create(
                        user_defined_model_type=udmt,
                        policy=policy_slug_map[p_slug],
                        defaults={"sort_order": 0},
                    )
                    if p_slug not in bundle_type_policy_slugs:
                        bundle_type_policy_slugs.append(p_slug)

        # Fallback: scope types that are in the request but NOT in the bundle
        # → link them to the same configs/policies as the bundle types.
        bundle_type_ids = set(str(t["id"]) for t in raw_bundle.get("udm_types", []))
        extra_scope_ids = parsed_scope_ids - bundle_type_ids
        if extra_scope_ids:
            # Determine configs to assign: one per bundle UDMType, by index.
            # If there's only one bundle type, assign its config to all extras.
            bundle_cfgs = list(bundle_type_cfg_map.values())
            for i, extra_id in enumerate(sorted(extra_scope_ids)):
                try:
                    udmt = UserDefinedModelType.objects.get(id=extra_id)
                except UserDefinedModelType.DoesNotExist:
                    continue
                # Pair by index; repeat last config if extras > bundle types
                cfg_to_assign = bundle_cfgs[min(i, len(bundle_cfgs) - 1)] if bundle_cfgs else None
                if cfg_to_assign:
                    udmt.field_config = cfg_to_assign
                    udmt.save(update_fields=["field_config"])
                for p_slug in bundle_type_policy_slugs:
                    if p_slug in policy_slug_map:
                        UserDefinedModelTypePolicy.objects.get_or_create(
                            user_defined_model_type=udmt,
                            policy=policy_slug_map[p_slug],
                            defaults={"sort_order": 0},
                        )

    return JsonResponse({
        "status": "ok",
        "imported_workflows": len(workflow_id_map),
        "imported_configs": len(config_id_map),
        "imported_policies": len(policy_slug_map),
    })


def _is_workflow_externally_used(workflow_id, scope_config_ids: set) -> bool:
    """Return True if this workflow is referenced by a FieldDefinition whose parent
    FieldConfig is NOT in scope_config_ids."""
    from userdefinedmodel.models import FieldDefinition
    return FieldDefinition.objects.filter(
        workflow_version__workflow_id=workflow_id
    ).exclude(
        version__config_id__in=scope_config_ids
    ).exists()


def _is_config_externally_used(config_id, scope_type_ids: set, scope_config_ids: set) -> bool:
    """Return True if this FieldConfig is referenced by a UDMType or submodel definition outside scope."""
    from userdefinedmodel.models import UserDefinedModelType, FieldDefinition
    # Direct UDMType usage
    if UserDefinedModelType.objects.filter(field_config_id=config_id).exclude(id__in=scope_type_ids).exists():
        return True
    # Submodel usage from configs not in scope
    if FieldDefinition.objects.filter(
        submodel_config__config_id=config_id
    ).exclude(version__config_id__in=scope_config_ids).exists():
        return True
    return False


def _update_workflow_from_data(wf_def, wf_data: dict) -> "WorkflowVersion":
    """Update an existing WorkflowDefinition's draft version from bundle data.

    Returns the updated (or newly created) published WorkflowVersion.
    """
    from userdefinedmodel.models import (
        WorkflowVersion, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    wf_def.name = wf_data["name"]
    wf_def.description = wf_data.get("description", "")
    wf_def.save()

    draft = WorkflowVersion.objects.filter(workflow=wf_def, status=WorkflowVersion.Status.DRAFT).first()
    if draft is None:
        published = WorkflowVersion.objects.filter(workflow=wf_def, status=WorkflowVersion.Status.PUBLISHED).first()
        if published:
            draft = published._create_draft_copy()
        else:
            draft = WorkflowVersion.objects.create(
                workflow=wf_def, status=WorkflowVersion.Status.DRAFT,
            )

    draft.virtual_node_positions = wf_data.get("virtual_node_positions") or {}
    draft.save()

    existing_states = {s.name: s for s in draft.states.all()}
    incoming_state_names = {s["name"] for s in wf_data.get("states", [])}
    state_map = {}
    for s_data in wf_data.get("states", []):
        if s_data["name"] in existing_states:
            s = existing_states[s_data["name"]]
            s.is_initial = s_data.get("is_initial", False)
            s.position_x = s_data.get("position_x", 0.0)
            s.position_y = s_data.get("position_y", 0.0)
            s.background_color = s_data.get("background_color", "#ffffff")
            s.save()
            s.translations.all().delete()
        else:
            s = WorkflowState.objects.create(
                version=draft, name=s_data["name"],
                is_initial=s_data.get("is_initial", False),
                position_x=s_data.get("position_x", 0.0),
                position_y=s_data.get("position_y", 0.0),
                background_color=s_data.get("background_color", "#ffffff"),
            )
        for lang, label in (s_data.get("label") or {}).items():
            WorkflowStateTranslation.objects.create(state=s, language=lang, label=label)
        state_map[s_data["name"]] = s
    for del_name in set(existing_states) - incoming_state_names:
        existing_states[del_name].delete()
    draft.transitions.all().delete()
    for tr_data in wf_data.get("transitions", []):
        tr = WorkflowTransition.objects.create(
            version=draft, name=tr_data["name"],
            from_state=state_map.get(tr_data["from_state"]) if tr_data.get("from_state") else None,
            from_undefined_only=tr_data.get("from_undefined_only", False),
            to_state=state_map[tr_data["to_state"]],
            source_handle=tr_data.get("source_handle", ""),
            target_handle=tr_data.get("target_handle", ""),
            properties=tr_data.get("properties") or {},
        )
        for lang, label in (tr_data.get("label") or {}).items():
            WorkflowTransitionTranslation.objects.create(transition=tr, language=lang, label=label)

    # Publish the draft so the returned version is usable by field definitions
    return draft.publish()


def _create_workflow_from_data(wf_data: dict) -> "WorkflowVersion":
    """Create a new WorkflowDefinition + published WorkflowVersion from bundle data.

    Returns the published WorkflowVersion.
    """
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowVersion, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    new_wf_def = WorkflowDefinition.objects.create(
        id=wf_data["id"],
        name=wf_data["name"],
        description=wf_data.get("description", ""),
    )
    draft = WorkflowVersion.objects.create(
        workflow=new_wf_def,
        status=WorkflowVersion.Status.DRAFT,
        virtual_node_positions=wf_data.get("virtual_node_positions") or {},
    )
    state_map = {}
    for s_data in wf_data.get("states", []):
        s = WorkflowState.objects.create(
            version=draft, name=s_data["name"],
            is_initial=s_data.get("is_initial", False),
            position_x=s_data.get("position_x", 0.0),
            position_y=s_data.get("position_y", 0.0),
            background_color=s_data.get("background_color", "#ffffff"),
        )
        for lang, label in (s_data.get("label") or {}).items():
            WorkflowStateTranslation.objects.create(state=s, language=lang, label=label)
        state_map[s_data["name"]] = s
    for tr_data in wf_data.get("transitions", []):
        tr = WorkflowTransition.objects.create(
            version=draft, name=tr_data["name"],
            from_state=state_map.get(tr_data["from_state"]) if tr_data.get("from_state") else None,
            from_undefined_only=tr_data.get("from_undefined_only", False),
            to_state=state_map[tr_data["to_state"]],
            source_handle=tr_data.get("source_handle", ""),
            target_handle=tr_data.get("target_handle", ""),
            properties=tr_data.get("properties") or {},
        )
        for lang, label in (tr_data.get("label") or {}).items():
            WorkflowTransitionTranslation.objects.create(transition=tr, language=lang, label=label)
    # Publish draft so field definitions can reference the published version
    return draft.publish()


def _clone_workflow(wf_def) -> "WorkflowVersion":
    """Deep-copy a WorkflowDefinition (clones the most recent version).

    Returns the published WorkflowVersion of the new clone.
    """
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowVersion, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    source_version = (
        wf_def.versions.filter(status="published").first()
        or wf_def.versions.filter(status="draft").first()
    )
    new_wf_def = WorkflowDefinition.objects.create(
        name=wf_def.name,
        description=wf_def.description,
    )
    if source_version is None:
        draft = WorkflowVersion.objects.create(
            workflow=new_wf_def, status=WorkflowVersion.Status.DRAFT,
        )
        return draft.publish()

    draft = WorkflowVersion.objects.create(
        workflow=new_wf_def,
        status=WorkflowVersion.Status.DRAFT,
        virtual_node_positions=source_version.virtual_node_positions or {},
    )
    state_map = {}
    for state in source_version.states.prefetch_related("translations").all():
        new_state = WorkflowState.objects.create(
            version=draft,
            name=state.name,
            is_initial=state.is_initial,
            position_x=state.position_x,
            position_y=state.position_y,
            background_color=state.background_color,
        )
        for t in state.translations.all():
            WorkflowStateTranslation.objects.create(state=new_state, language=t.language, label=t.label)
        state_map[state.name] = new_state
    for trans in source_version.transitions.prefetch_related("translations").select_related("from_state", "to_state").all():
        new_trans = WorkflowTransition.objects.create(
            version=draft,
            name=trans.name,
            from_state=state_map.get(trans.from_state.name) if trans.from_state else None,
            from_undefined_only=trans.from_undefined_only,
            to_state=state_map[trans.to_state.name],
            source_handle=trans.source_handle,
            target_handle=trans.target_handle,
        )
        for t in trans.translations.all():
            WorkflowTransitionTranslation.objects.create(transition=new_trans, language=t.language, label=t.label)
    return draft.publish()


def _clone_field_config(cfg, languages_data: list, id=None) -> tuple:
    """Deep-copy a FieldConfig (without versions). Returns (new_config, new_draft)."""
    from userdefinedmodel.models import FieldConfig, ConfigLanguage, ConfigVersion
    kwargs = {"name": cfg.name, "description": cfg.description}
    if id is not None:
        kwargs["id"] = id
    new_cfg = FieldConfig.objects.create(**kwargs)
    for lang in languages_data:
        ConfigLanguage.objects.create(
            config=new_cfg,
            code=lang["code"],
            label=lang["label"],
            is_default=lang["is_default"],
            sort_order=lang["sort_order"],
        )
    new_draft = ConfigVersion.objects.create(config=new_cfg, status=ConfigVersion.Status.DRAFT)
    return new_cfg, new_draft


def _toposort_configs(fc_by_id: dict) -> list[str]:
    """Return config IDs in dependency order (leaf submodels first)."""
    result = []
    visited = set()

    def visit(cfg_id):
        if cfg_id in visited:
            return
        visited.add(cfg_id)
        fc_data = fc_by_id.get(cfg_id)
        if fc_data:
            for fd in (fc_data.get("draft", {}).get("data_fields") or []):
                sub_id = fd.get("submodel_config_version_id")
                if sub_id and str(sub_id) in fc_by_id:
                    # submodel_config_version_id is a FieldConfig UUID in this bundle
                    # — visit the dependency first so it ends up earlier in the list.
                    visit(str(sub_id))
        result.append(cfg_id)

    for cfg_id in fc_by_id:
        visit(cfg_id)
    return result


def _apply_draft_fields(
    draft,
    draft_data: dict,
    workflow_id_map: dict,
    config_id_map: dict,
    bundle_config_ids: set,
    pending_submodel_refs: list | None = None,
):
    """Populate a ConfigVersion's field_definitions from bundle draft data.

    Remaps workflow_definition_id and submodel_config_version_id through
    the id maps built during import so cloned/new objects are referenced correctly.

    When submodel_config_version_id in the bundle is a FieldConfig UUID (i.e. it
    exists as a key in config_id_map), the field is created with submodel_config=None
    and a (FieldDefinition, cfg_id) tuple is appended to pending_submodel_refs for
    the caller to resolve after all drafts have been published.
    """
    from userdefinedmodel.models import (
        ConfigVersion, DataField, FormElement, FormElementTranslation, FormElementBinding,
    )
    draft.form_elements.all().delete()  # cascades to bindings + translations
    draft.field_definitions.all().delete()

    # Support both the new shape (data_fields + form_elements) and the legacy
    # shape (fields: mixed data + structural).
    data_fields = draft_data.get("data_fields") or []
    form_elements = draft_data.get("form_elements") or []
    legacy_fields = draft_data.get("fields")
    if legacy_fields is not None and not data_fields and not form_elements:
        structural_set = {"tab_container","tab","save_button","hstack","hstack_group","tab_prev","tab_next"}
        for fd_data in legacy_fields:
            if fd_data.get("data_type") in structural_set:
                form_elements.append({
                    "slug": fd_data["slug"],
                    "element_type": fd_data["data_type"],
                    "parent_slug": fd_data.get("parent_slug"),
                    "sort_order": fd_data.get("sort_order", 0),
                    "is_preview": fd_data.get("is_preview", False),
                    "labels": fd_data.get("labels"),
                    "help_texts": fd_data.get("help_texts") or {},
                    "type_config": fd_data.get("type_config") or {},
                    "bindings": [],
                })
            else:
                data_fields.append(fd_data)
                form_elements.append({
                    "slug": fd_data["slug"],
                    "element_type": "field",
                    "parent_slug": fd_data.get("parent_slug"),
                    "sort_order": fd_data.get("sort_order", 0),
                    "is_preview": fd_data.get("is_preview", False),
                    "labels": fd_data.get("labels"),
                    "help_texts": fd_data.get("help_texts") or {},
                    "type_config": {},
                    "bindings": [{"data_field_slug": fd_data["slug"], "role": ""}],
                })

    def _resolve_wf(wf_def_id):
        resolved_wf_ver = None
        if wf_def_id:
            wf_def_id_str = str(wf_def_id)
            if wf_def_id_str in workflow_id_map:
                resolved_wf_ver = workflow_id_map[wf_def_id_str]
            else:
                from userdefinedmodel.models import WorkflowVersion
                resolved_wf_ver = WorkflowVersion.objects.filter(
                    workflow_id=wf_def_id_str, status=WorkflowVersion.Status.PUBLISHED
                ).first()
                if resolved_wf_ver is None:
                    resolved_wf_ver = WorkflowVersion.objects.filter(id=wf_def_id_str).first()
        return resolved_wf_ver

    def _resolve_sub(sub_ver_id):
        resolved_sub_ver = None
        deferred_cfg_id = None
        if sub_ver_id:
            sub_ver_id_str = str(sub_ver_id)
            if sub_ver_id_str in config_id_map:
                deferred_cfg_id = sub_ver_id_str
            else:
                resolved_sub_ver = _resolve_submodel_version(sub_ver_id_str, config_id_map, bundle_config_ids)
        return resolved_sub_ver, deferred_cfg_id

    # Create data fields
    field_map = {}
    for fd_data in data_fields:
        wf_def_id = fd_data.get("workflow_definition_id") or fd_data.get("workflow_version_id")
        resolved_wf_ver = _resolve_wf(wf_def_id)
        resolved_sub_ver, deferred_cfg_id = _resolve_sub(fd_data.get("submodel_config_version_id"))

        fd = DataField.objects.create(
            version=draft,
            slug=fd_data["slug"],
            data_type=fd_data["data_type"],
            is_localized=fd_data.get("is_localized", False),
            submodel_config=resolved_sub_ver,
            workflow_version=resolved_wf_ver,
            type_config=fd_data.get("type_config") or {},
        )
        field_map[fd_data["slug"]] = fd
        if deferred_cfg_id is not None and pending_submodel_refs is not None:
            pending_submodel_refs.append((fd, deferred_cfg_id))

        default = fd_data.get("default")
        if default is not None:
            _create_field_default(fd, default, fd_data.get("is_localized", False))

    # Create form elements + translations + bindings
    element_map = {}
    for el_data in form_elements:
        el = FormElement.objects.create(
            version=draft,
            slug=el_data["slug"],
            element_type=el_data["element_type"],
            parent=None,
            sort_order=el_data.get("sort_order", 0),
            is_preview=el_data.get("is_preview", False),
            type_config=el_data.get("type_config") or {},
        )
        element_map[el_data["slug"]] = el
        for lang, label in (el_data.get("labels") or {}).items():
            help_text = (el_data.get("help_texts") or {}).get(lang, "")
            FormElementTranslation.objects.create(element=el, language=lang, label=label, help_text=help_text)
        for b in el_data.get("bindings") or []:
            df = field_map.get(b["data_field_slug"])
            if df is not None:
                FormElementBinding.objects.create(form_element=el, data_field=df, role=b.get("role", ""))

    # Resolve parents
    for el_data in form_elements:
        parent_slug = el_data.get("parent_slug")
        if parent_slug:
            parent = element_map.get(parent_slug)
            if parent is not None:
                element_map[el_data["slug"]].parent = parent
                element_map[el_data["slug"]].save(update_fields=["parent"])


def _resolve_submodel_version(sub_ver_id: str, config_id_map: dict, bundle_config_ids: set):
    """Given a submodel_config_version_id from the bundle, find the ConfigVersion to use.

    If the version's config was cloned (is in config_id_map with a new config), return
    the new draft of that config. Otherwise return the original version.
    """
    from userdefinedmodel.models import ConfigVersion
    try:
        original_version = ConfigVersion.objects.select_related("config").get(id=sub_ver_id)
    except ConfigVersion.DoesNotExist:
        return None

    orig_config_id = str(original_version.config_id)
    if orig_config_id in config_id_map:
        new_cfg = config_id_map[orig_config_id]
        if new_cfg.id != original_version.config_id:
            # Config was cloned; use the new draft
            try:
                return ConfigVersion.objects.get(config=new_cfg, status=ConfigVersion.Status.DRAFT)
            except ConfigVersion.DoesNotExist:
                pass
    return original_version


# ─── Bulk migration ───────────────────────────────────────────────────────────

@router.post("/bulk-migrations/preview/", auth=django_auth)
def bulk_migration_preview(
    request,
    source_version_id: uuid.UUID,
    target_version_id: uuid.UUID,
    type_filter_id: Optional[uuid.UUID] = None,
):
    from userdefinedmodel.models import ConfigVersion, UserDefinedModelEntity
    if denied := _require_perms(request, "userdefinedmodel.view_bulkmigrationplan"):
        return denied
    try:
        src = ConfigVersion.objects.get(id=source_version_id)
        tgt = ConfigVersion.objects.get(id=target_version_id)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "Version not found"}, status=404)
    qs = UserDefinedModelEntity.objects.filter(config_version=src)
    if type_filter_id:
        qs = qs.filter(user_defined_model_type_id=type_filter_id)
    return JsonResponse({
        "affected_entity_count": qs.count(),
        "source_version_id": str(src.id),
        "target_version_id": str(tgt.id),
    })


@router.post("/bulk-migrations/", response={201: BulkMigrationOut}, auth=django_auth)
def create_bulk_migration(request, payload: BulkMigrationCreateIn):
    from userdefinedmodel.models import (
        ConfigVersion, UserDefinedModelType,
        BulkMigrationPlan, BulkMigrationFieldMapping,
        BulkMigrationSubmodelMapping, BulkMigrationSubmodelFieldMapping,
        BulkMigrationWorkflowStateMapping,
    )
    if denied := _require_perms(request, "userdefinedmodel.add_bulkmigrationplan"):
        return denied
    try:
        src = ConfigVersion.objects.get(id=payload.source_version_id)
        tgt = ConfigVersion.objects.get(id=payload.target_version_id)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "Version not found"}, status=404)

    type_filter = None
    if payload.user_defined_model_type_filter_id:
        try:
            type_filter = UserDefinedModelType.objects.get(id=payload.user_defined_model_type_filter_id)
        except UserDefinedModelType.DoesNotExist:
            return JsonResponse({"detail": "Type filter not found"}, status=404)

    with transaction.atomic():
        plan = BulkMigrationPlan.objects.create(
            source_version=src, target_version=tgt,
            user_defined_model_type_filter=type_filter,
            created_by=request.user,
        )
        src_fields = {f.slug: f for f in src.field_definitions.select_related("submodel_config").all()}
        tgt_fields = {f.slug: f for f in tgt.field_definitions.all()}
        for mapping in payload.field_mappings:
            src_field = src_fields.get(mapping.source_field_slug)
            if src_field:
                BulkMigrationFieldMapping.objects.create(
                    plan=plan, source_field=src_field,
                    action=mapping.action.value,
                    target_field=tgt_fields.get(mapping.target_field_slug) if mapping.target_field_slug else None,
                )

        for sm in payload.submodel_mappings:
            src_field = src_fields.get(sm.source_parent_field_slug)
            if not src_field or not src_field.submodel_config:
                raise ApiError(400, {"detail": f"Source submodel field '{sm.source_parent_field_slug}' not found or has no submodel config"})
            try:
                tgt_submodel_version = ConfigVersion.objects.get(id=sm.target_submodel_version_id)
            except ConfigVersion.DoesNotExist:
                raise ApiError(404, {"detail": "Target submodel version not found"})
            submodel_mapping = BulkMigrationSubmodelMapping.objects.create(
                plan=plan,
                source_parent_field=src_field,
                target_submodel_version=tgt_submodel_version,
            )
            src_sub_fields = {f.slug: f for f in src_field.submodel_config.field_definitions.all()}
            tgt_sub_fields = {f.slug: f for f in tgt_submodel_version.field_definitions.all()}
            for fm in sm.field_mappings:
                sub_src = src_sub_fields.get(fm.source_field_slug)
                if sub_src:
                    BulkMigrationSubmodelFieldMapping.objects.create(
                        submodel_mapping=submodel_mapping,
                        source_field=sub_src,
                        action=fm.action.value,
                        target_field=tgt_sub_fields.get(fm.target_field_slug) if fm.target_field_slug else None,
                    )

        for wm in payload.workflow_state_mappings:
            for state_mapping in wm.state_mappings:
                BulkMigrationWorkflowStateMapping.objects.create(
                    plan=plan,
                    field_slug=wm.field_slug,
                    from_state=state_mapping.from_state,
                    to_state=state_mapping.to_state,
                )

    return 201, BulkMigrationOut(
        id=plan.id, status=BulkMigrationStatus(plan.status),
        source_version_id=plan.source_version_id,
        target_version_id=plan.target_version_id,
        user_defined_model_type_filter_id=plan.user_defined_model_type_filter_id,
        total_entities=plan.total_entities,
        done_entities=plan.done_entities,
        failed_entities=plan.failed_entities,
        executed_at=plan.executed_at.isoformat() if plan.executed_at else None,
        error_message=plan.error_message or "",
    )


@router.get("/bulk-migrations/{plan_id}/", response=BulkMigrationOut, auth=django_auth)
def get_bulk_migration(request, plan_id: uuid.UUID):
    from userdefinedmodel.models import BulkMigrationPlan
    if denied := _require_perms(request, "userdefinedmodel.view_bulkmigrationplan"):
        return denied
    try:
        plan = BulkMigrationPlan.objects.get(id=plan_id)
    except BulkMigrationPlan.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return BulkMigrationOut(
        id=plan.id, status=BulkMigrationStatus(plan.status),
        source_version_id=plan.source_version_id,
        target_version_id=plan.target_version_id,
        user_defined_model_type_filter_id=plan.user_defined_model_type_filter_id,
        total_entities=plan.total_entities,
        done_entities=plan.done_entities,
        failed_entities=plan.failed_entities,
        executed_at=plan.executed_at.isoformat() if plan.executed_at else None,
        error_message=plan.error_message or "",
    )


@router.post("/bulk-migrations/{plan_id}/execute/", auth=django_auth)
def execute_bulk_migration_plan(request, plan_id: uuid.UUID):
    from userdefinedmodel.models import BulkMigrationPlan
    from userdefinedmodel.tasks import execute_bulk_migration
    if denied := _require_perms(request, "userdefinedmodel.change_bulkmigrationplan"):
        return denied
    try:
        plan = BulkMigrationPlan.objects.get(id=plan_id)
    except BulkMigrationPlan.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if plan.status in (BulkMigrationPlan.Status.RUNNING, BulkMigrationPlan.Status.DONE):
        return JsonResponse({"detail": f"Plan is already {plan.status}"}, status=409)
    execute_bulk_migration.delay(str(plan_id))
    return JsonResponse({"status": "accepted", "plan_id": str(plan_id)}, status=202)

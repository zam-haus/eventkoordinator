"""Zip the on-disk configuration folder and import it through the normal bundle path.

This is the seeding path for dev/CI and fresh installs: files, ZIP and import all
stay a single code path, so what a developer edits under
``documentation/configuration/`` is exactly what an operator would upload in
UDM Admin → Export / Import.
"""
import io
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from userdefinedmodel.api_bundle import (
    BundleImportError,
    build_identity_migration_plan,
    import_bundle_bytes,
    plan_identity_migration,
)
from userdefinedmodel.mailtemplates import get_environment

DEFAULT_DIR = Path(settings.BASE_DIR).parent / "documentation" / "configuration"

#: Files packed into the bundle ZIP, relative to the configuration directory.
BUNDLE_MEMBERS = ("UDM_BUNDLE.json", "policies", "templates")


class Command(BaseCommand):
    help = "Import documentation/configuration/ as a UDM bundle, optionally migrating entities."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=str(DEFAULT_DIR), help="Configuration directory to pack.")
        parser.add_argument(
            "--scope-type-ids", default="",
            help="Comma-separated UDM Type UUIDs; defaults to the bundle's own scope.",
        )
        parser.add_argument(
            "--migrate-entities", action="store_true",
            help="After import, migrate existing entities onto the new config versions "
                 "(synchronously, no Celery). Only possible for identity field mappings.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would happen and write nothing.",
        )

    def handle(self, *args, **options):
        directory = Path(options["dir"]).resolve()
        if not directory.is_dir():
            raise CommandError(f"Not a directory: {directory}")

        zip_bytes = self._build_zip(directory)

        if options["dry_run"]:
            self.stdout.write(f"Would import {len(zip_bytes)} bytes from {directory}")
            if options["migrate_entities"]:
                self._report_migration_candidates()
            return

        try:
            result = import_bundle_bytes(zip_bytes, options["scope_type_ids"])
        except BundleImportError as exc:
            raise CommandError(str(exc))

        self.stdout.write(
            "Imported: {imported_configs} configs, {imported_workflows} workflows, "
            "{imported_policies} policies, {imported_mail_templates} mail templates".format(**result)
        )

        if options["migrate_entities"]:
            failed = self._migrate_entities(result.get("config_ids", []))
            if failed:
                raise CommandError(f"{failed} entities failed to migrate; see the log above.")

    # ── ZIP building ─────────────────────────────────────────────────────────

    def _build_zip(self, directory: Path) -> bytes:
        """Pack the configuration directory in memory, validating templates first."""
        buf = io.BytesIO()
        written = 0
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for member in BUNDLE_MEMBERS:
                path = directory / member
                if path.is_file():
                    zf.writestr(member, path.read_bytes())
                    written += 1
                elif path.is_dir():
                    for child in sorted(path.rglob("*")):
                        if not child.is_file():
                            continue
                        if member == "templates" and child.suffix == ".j2":
                            self._check_template_syntax(child)
                        zf.writestr(f"{member}/{child.relative_to(path).as_posix()}", child.read_bytes())
                        written += 1
        if not written:
            raise CommandError(f"No bundle files found in {directory}")
        return buf.getvalue()

    def _check_template_syntax(self, path: Path) -> None:
        """Fail before writing anything if a template body does not compile."""
        autoescape = path.name.endswith(".html.j2")
        try:
            get_environment(autoescape).parse(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CommandError(f"{path}: {type(exc).__name__}: {exc}")

    # ── Entity migration ─────────────────────────────────────────────────────

    def _migration_candidates(self, config_ids):
        """Yield (config, src_version, tgt_version, entity_count) needing migration."""
        from userdefinedmodel.models import ConfigVersion, UserDefinedModelEntity

        for cfg_id in config_ids:
            try:
                target = ConfigVersion.objects.get(
                    config_id=cfg_id, status=ConfigVersion.Status.PUBLISHED
                )
            except ConfigVersion.DoesNotExist:
                continue
            sources = (
                ConfigVersion.objects.filter(config_id=cfg_id)
                .exclude(id=target.id)
                .exclude(status=ConfigVersion.Status.DRAFT)
            )
            for source in sources:
                count = UserDefinedModelEntity.objects.filter(config_version=source).count()
                if count:
                    yield source, target, count

    def _report_migration_candidates(self):
        from userdefinedmodel.models import ConfigVersion

        config_ids = list(
            ConfigVersion.objects.filter(status=ConfigVersion.Status.PUBLISHED)
            .values_list("config_id", flat=True)
            .distinct()
        )
        for source, target, count in self._migration_candidates(config_ids):
            _, unresolvable = plan_identity_migration(source, target)
            if unresolvable:
                self.stdout.write(f"  {source.config_id}: {count} entities — NOT migratable:")
                for reason in unresolvable:
                    self.stdout.write(f"    - {reason}")
            else:
                self.stdout.write(f"  {source.config_id}: would migrate {count} entities")

    def _migrate_entities(self, config_ids) -> int:
        """Migrate entities onto the freshly published versions. Returns failure count."""
        from userdefinedmodel.models import BulkMigrationPlan
        from userdefinedmodel.tasks import run_bulk_migration

        failed = 0
        for source, target, count in self._migration_candidates(config_ids):
            _, unresolvable = plan_identity_migration(source, target)
            if unresolvable:
                self.stdout.write(self.style.WARNING(
                    f"Skipping {count} entities on config {source.config_id}: "
                    f"the mapping is ambiguous and needs a human decision."
                ))
                for reason in unresolvable:
                    self.stdout.write(f"  - {reason}")
                self.stdout.write("  Use UDM Admin → Bulk Migration to map these by hand.")
                continue

            plan = build_identity_migration_plan(source, target)
            # run_bulk_migration is deliberately the plain function, not the
            # Celery task: this must work without a worker running.
            run_bulk_migration(str(plan.id))
            plan = BulkMigrationPlan.objects.get(id=plan.id)
            failed += plan.failed_entities
            self.stdout.write(
                f"Migrated {plan.done_entities}/{plan.total_entities} entities "
                f"on config {source.config_id} ({plan.failed_entities} failed)"
            )
        return failed

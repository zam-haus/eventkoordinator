import os
import uuid

from django.contrib import admin, messages
from django.contrib.admin import ModelAdmin
from django.core.files.base import ContentFile
from django.http import Http404, HttpResponseRedirect
from django.urls import path, reverse
from polymorphic.admin import PolymorphicParentModelAdmin
from simple_history.admin import SimpleHistoryAdmin
from viewflow import fsm

from . import models
from .flows import ProposalFlow
from .models import SyncBaseTarget


def _copy_imagefield(field):
    """Read an ImageField and return (new_filename, ContentFile) for copying, or (None, None)."""
    if not field or not field.name:
        return None, None
    try:
        ext = os.path.splitext(field.name)[1]
        new_name = f"{uuid.uuid4()}{ext}"
        field.open("rb")
        content = ContentFile(field.read())
        field.close()
        return new_name, content
    except Exception:
        return None, None


class LinkedEventsInline(admin.TabularInline):
    model = models.Event
    extra = 0
    ordering = ("start_time",)
    fields = ("name", "series", "start_time", "end_time", "status")
    readonly_fields = ("name", "series", "start_time", "end_time", "status")
    show_change_link = True
    verbose_name = "Linked Event"
    verbose_name_plural = "Linked Events"

    def has_add_permission(self, request, obj=None):
        return False


class SpeakerInline(admin.TabularInline):
    model = models.Speaker
    extra = 1
    ordering = ("sort_order",)
    fields = ("display_name", "email", "role", "sort_order")


@admin.register(models.Series)
class SeriesAdmin(SimpleHistoryAdmin):
    list_display = ("id", "name", "created_at", "updated_at")
    search_fields = ("name",)
    ordering = ("name",)


# LinkedSyncItemsInline (apiv1's own SyncBaseItem polymorphic inline) was
# removed here: PretixSyncItem, CalDAVSyncItem, and IcalCalenderSyncItem all
# now subclass sync_core.models.SyncBaseItem (events-and-sync.md §3, Step
# 11), not this app's own SyncBaseItem, so none of them have a
# `related_event` FK to inline under an apiv1 Event admin any more — apiv1's
# own SyncBaseItem has no concrete subclasses left. They are managed in
# sync_pretix/admin.py, sync_caldav/admin.py, and sync_ical/admin.py instead.


@admin.register(models.Event)
class EventAdmin(SimpleHistoryAdmin):
    list_display = ("id", "name", "series", "start_time", "end_time")
    list_filter = ("series", "tag")
    search_fields = ("name", "tag")
    ordering = ("start_time",)


@admin.register(models.SubmissionType)
class SubmissionTypeAdmin(SimpleHistoryAdmin):
    list_display = ("code", "label", "is_active", "sort_order")
    list_editable = ("label", "is_active", "sort_order")
    search_fields = ("code", "label")


@admin.register(models.ProposalLanguage)
class ProposalLanguageAdmin(SimpleHistoryAdmin):
    list_display = ("code", "label", "is_active", "sort_order")
    list_editable = ("label", "is_active", "sort_order")
    search_fields = ("code", "label")


@admin.register(models.ProposalArea)
class ProposalAreaAdmin(SimpleHistoryAdmin):
    list_display = ("code", "label", "is_active", "sort_order")
    list_editable = ("label", "is_active", "sort_order")
    search_fields = ("code", "label")
    # PretixSyncTargetAreaAssociationInline removed here: that model no longer
    # FKs to apiv1.ProposalArea (events-and-sync.md §3, Step 11 — decoupled to
    # a plain area_code string). Managed in sync_pretix/admin.py instead.


@admin.register(models.Speaker)
class SpeakerAdmin(SimpleHistoryAdmin):
    list_display = (
        "display_name",
        "email",
        "proposal",
        "role",

        "created_at",
    )
    search_fields = ("display_name", "email", "proposal__title")
    readonly_fields = ("created_at", "updated_at")
    list_filter = ("role",)
    raw_id_fields = ("proposal",)


@admin.register(models.Proposal)
class ProposalAdmin(fsm.FlowAdminMixin, SimpleHistoryAdmin):
    flow_state = ProposalFlow.status

    def get_object_flow(self, request, obj):
        return ProposalFlow(obj)

    def get_urls(self):
        return [
            path(
                "<path:object_id>/copy/",
                self.admin_site.admin_view(self.copy_view),
                name="apiv1_proposal_copy",
            ),
        ] + super().get_urls()

    def _do_copy(self, original):
        editors = list(original.editors.all())
        speakers = list(original.speakers.all())
        photo_name, photo_content = _copy_imagefield(original.photo)

        copy = models.Proposal.objects.get(pk=original.pk)
        copy.pk = None
        copy._state.adding = True
        copy.status = models.Proposal.Status.DRAFT
        copy.moderation_comment = ""
        copy.photo = None
        copy.save()

        if photo_content:
            copy.photo.save(photo_name, photo_content, save=True)

        copy.editors.set(editors)

        for spk in speakers:
            spk_photo_name, spk_photo_content = _copy_imagefield(spk.profile_picture)
            spk.pk = None
            spk._state.adding = True
            spk.proposal = copy
            spk.profile_picture = None
            spk.save()
            if spk_photo_content:
                spk.profile_picture.save(spk_photo_name, spk_photo_content, save=True)

        return copy

    def copy_view(self, request, object_id):
        original = self.get_object(request, object_id)
        if original is None:
            raise Http404
        copy = self._do_copy(original)
        self.message_user(
            request,
            f"Proposal '{original.title}' was copied successfully.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect(
            reverse("admin:apiv1_proposal_change", args=[copy.pk])
        )

    @admin.action(description="Copy selected proposals")
    def copy_proposals(self, request, queryset):
        count = 0
        for proposal in queryset:
            self._do_copy(proposal)
            count += 1
        self.message_user(request, f"Copied {count} proposal(s).", messages.SUCCESS)

    actions = ["copy_proposals"]

    list_display = (
        "id",
        "title",
        "submission_type",
        "status",
        "call",
        "owner",
        "is_basic_course",
        "max_participants",
        "created_at",
    )
    list_filter = ("submission_type", "status", "call", "area", "language", "is_basic_course")
    search_fields = ("title", "abstract", "description")
    readonly_fields = ("created_at", "updated_at", "status")
    raw_id_fields = ("owner",)
    filter_horizontal = ("editors",)
    inlines = (SpeakerInline, LinkedEventsInline)
    fieldsets = (
        (
            "General",
            {
                "fields": (
                    "title",
                    "submission_type",
                    "area",
                    "language",
                    "abstract",
                    "description",
                    "internal_notes",
                )
            },
        ),
        (
            "Details",
            {
                "fields": (
                    "occurrence_count",
                    "is_basic_course",
                    "max_participants",
                    "material_cost_eur",
                    "preferred_dates",
                )
            },
        ),
        ("People", {"fields": ("owner", "editors")}),
        ("Call", {"fields": ("call",)}),
        ("Status", {"fields": ("status",)}),
    )


@admin.register(models.Call)
class CallAdmin(SimpleHistoryAdmin):
    list_display = ("id", "title", "submission_deadline", "is_active", "responsible_name")
    list_filter = ("is_active",)
    search_fields = ("title", "responsible_name", "responsible_email")
    list_editable = ("is_active",)
    fieldsets = (
        (
            "Allgemein",
            {"fields": ("title", "description", "is_active")},
        ),
        (
            "Zeitraum",
            {"fields": ("execution_period_start", "execution_period_end", "submission_deadline", "print_deadline")},
        ),
        (
            "Verantwortlich",
            {"fields": ("responsible_name", "responsible_email")},
        ),
    )


@admin.register(models.ProposalReview)
class ProposalReviewAdmin(ModelAdmin):
    list_display = ("id", "proposal", "kind", "status", "reviewer", "requested_by", "created_at", "completed_at")
    list_filter = ("kind", "status", "reviewer_is_system", "migrated")
    search_fields = ("proposal__title", "reviewer__email", "requested_by__email", "group_code", "comment")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("proposal", "reviewer", "requested_by")
    fieldsets = (
        ("General", {"fields": ("id", "proposal", "kind", "status", "comment")}),
        ("Reviewer", {"fields": ("reviewer", "reviewer_is_system", "group_code")}),
        ("Request", {"fields": ("requested_by", "requested_directly", "requested_via_groups", "requested_at", "completed_at")}),
        ("History", {"fields": ("previous_status", "previous_comment", "migrated")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(SyncBaseTarget)
class SyncBaseTargetAdmin(PolymorphicParentModelAdmin, SimpleHistoryAdmin):
    list_display = (
        "id",
        "type",
        "created_at",
    )
    # CalDAVSyncTarget and PretixSyncTarget removed here: both now subclass
    # sync_core.models.SyncBaseTarget (events-and-sync.md §3, Step 11), not
    # this app's own SyncBaseTarget, so neither is a valid polymorphic child
    # of this admin's base model any more — apiv1's own SyncBaseTarget has no
    # concrete subclasses left.
    child_models = ()
    readonly_fields = ("id", "type", "created_at")
    fields = ("id", "type", "created_at")

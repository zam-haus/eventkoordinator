"""Staging file routes: /staging-files/..."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional

from django.http import JsonResponse
from django.utils.timezone import now
from ninja import File, Router, UploadedFile
from ninja.security import django_auth

from userdefinedmodel.schemas import StagingFileOut

router = Router(auth=django_auth)


@router.post("/staging-files/", response={201: StagingFileOut}, auth=django_auth)
def upload_staging_file(
    request,
    file: UploadedFile = File(...),
    intended_field_id: Optional[uuid.UUID] = None,
):
    from userdefinedmodel.models.node import StagingFile
    staging = StagingFile.objects.create(
        uploader=request.user,
        file=file,
        original_name=file.name,
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=file.size,
        expires_at=now() + timedelta(hours=24),
        intended_field_id=intended_field_id,
    )
    return 201, StagingFileOut(
        staging_id=staging.id,
        original_name=staging.original_name,
        mime_type=staging.mime_type,
        size_bytes=staging.size_bytes,
        expires_at=staging.expires_at.isoformat(),
    )


@router.delete("/staging-files/{staging_id}/", auth=django_auth)
def delete_staging_file(request, staging_id: uuid.UUID):
    from userdefinedmodel.models.node import StagingFile
    try:
        staging = StagingFile.objects.get(id=staging_id, uploader=request.user)
    except StagingFile.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    staging.file.delete(save=False)
    staging.delete()
    return JsonResponse({}, status=204)

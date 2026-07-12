"""
userdefinedmodel API — mounted at /api/udm/ in project urls.py.
All endpoints use typed response schemas for OpenAPI compatibility:
  - Success: response={N: SchemaType} + return (N, schema_obj)
  - Errors:  return JsonResponse({...}, status=N) — passes through Ninja unchanged
"""
from __future__ import annotations

from ninja import NinjaAPI
from ninja.security import django_auth

from userdefinedmodel.api_helpers import ApiError
from userdefinedmodel.api_autocomplete import router as autocomplete_router
from userdefinedmodel.api_bundle import router as bundle_router
from userdefinedmodel.api_configs import router as configs_router
from userdefinedmodel.api_entities import router as entities_router
from userdefinedmodel.api_policies import router as policies_router
from userdefinedmodel.api_staging import router as staging_router
from userdefinedmodel.api_types import router as types_router
from userdefinedmodel.api_workflows import router as workflows_router

api = NinjaAPI(urls_namespace="udm", auth=django_auth)


@api.exception_handler(ApiError)
def _handle_api_error(request, exc: ApiError):
    return api.create_response(request, exc.payload, status=exc.status)

api.add_router("", configs_router)
api.add_router("", types_router)
api.add_router("", workflows_router)
api.add_router("", policies_router)
api.add_router("", entities_router)
api.add_router("", staging_router)
api.add_router("", autocomplete_router)
api.add_router("", bundle_router)

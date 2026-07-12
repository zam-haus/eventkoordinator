"""
API router for OpenID User Management.

Provides endpoints for user management operations including authentication,
profile retrieval, and user administration.
"""

import logging
from uuid import UUID

from django.apps import apps
from django.db.models import Model
from ninja import Router
from django.http import HttpRequest

import openid_user_management
from apiv1.api_utils import api_permission_required
from openid_user_management.models import OpenIDUser
from openid_user_management.schemas import (
    OpenIDUserOut,
    OpenIDUserUpdate,
    OpenIDUserLogin,
    OpenIDUserProfile,
    ErrorOut,
    PermissionsOut,
    PermissionIn,
    SudoModeIn,
    SudoModeOut,
)
from openid_user_management.sudo import SUDO_SESSION_KEY, is_sudo_active

logger = logging.getLogger(__name__)
router = Router()


@router.get(
    "/users/{user_id}",
    response={200: OpenIDUserOut, 404: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_required((openid_user_management, "view", OpenIDUser))
def get_user(request: HttpRequest, user_id: UUID) -> tuple:
    """
    Retrieve a user by UUID.

    Returns user information if found, 404 if user does not exist.
    """
    try:
        user = OpenIDUser.objects.get(id=user_id)
        return 200, _user_to_out(user)
    except OpenIDUser.DoesNotExist:
        return 404, ErrorOut(code="users.notFound")
    except Exception as e:
        logger.error(f"Failed to retrieve user: {str(e)}")
        return 404, ErrorOut(code="users.notFound")


@router.get(
    "/users/email/{email}",
    response={200: OpenIDUserOut, 404: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_required((openid_user_management, "view", OpenIDUser))
def get_user_by_email(request: HttpRequest, email: str) -> tuple:
    """
    Retrieve a user by email address.

    Returns user information if found, 404 if user does not exist.
    """
    try:
        user = OpenIDUser.objects.get(email=email)
        return 200, _user_to_out(user)
    except OpenIDUser.DoesNotExist:
        return 404, ErrorOut(code="users.notFound")
    except Exception as e:
        logger.error(f"Failed to retrieve user: {str(e)}")
        return 404, ErrorOut(code="users.notFound")


@router.get(
    "/me",
    response={200: OpenIDUserProfile, 401: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
def get_current_user_profile(request: HttpRequest) -> tuple:
    """
    Retrieve the current authenticated user's profile.

    Returns 401 if user is not authenticated.
    Note: This endpoint requires authentication middleware setup.
    """
    if not request.user or not request.user.is_authenticated:
        return 401, ErrorOut(code="auth.notAuthenticated")

    try:
        user = OpenIDUser.objects.get(id=request.user.id)
        return 200, OpenIDUserProfile(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.get_full_name(),
            short_name=user.get_short_name(),
            picture=user.picture,
            locale=user.locale,
            phone_number=user.phone_number,
        )
    except Exception as e:
        logger.error(f"Failed to retrieve current user: {str(e)}")
        return 401, ErrorOut(code="users.notFound")


@router.get(
    "/permissions",
    response={200: PermissionsOut, 401: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
def get_current_user_permissions(request: HttpRequest) -> tuple:
    """
    Retrieve the current authenticated user's permissions and access levels.

    Returns:
    - is_authenticated: Whether the user is authenticated
    - is_staff: Whether the user can access the admin interface
    - is_superuser: Whether the user has all permissions
    - is_active: Whether the user account is active
    - permissions: List of user's permission codenames (e.g., 'add_proposal', 'change_proposal')

    Returns 401 if user is not authenticated.
    """
    if not request.user or not request.user.is_authenticated:
        return 401, ErrorOut(code="auth.notAuthenticated")

    try:
        if not isinstance(request.user, OpenIDUser):
            return 401, ErrorOut(code="auth.notAuthenticated")
        user : OpenIDUser = request.user

        # Get all permission codenames for the user
        # Includes permissions from groups and direct user permissions
        permissions = list(
            user.user_permissions.values_list("codename", flat=True)
        ) + list(user.groups.values_list("permissions__codename", flat=True).distinct())

        return 200, PermissionsOut(
            is_authenticated=True,
            is_staff=user.is_staff,
            is_superuser=user.is_superuser,
            is_active=user.is_active,
            sudo_mode=is_sudo_active(request),
            permissions=list(set(p for p in permissions if p is not None)),  # Remove duplicates and None values
        )
    except Exception as e:
        logger.error(f"Failed to retrieve user permissions: {str(e)}")
        raise Exception("Failed to retrieve permissions") from e


@router.post(
    "/sudo",
    response={200: SudoModeOut, 401: ErrorOut, 403: ErrorOut},
)
def set_sudo_mode(request: HttpRequest, payload: SudoModeIn) -> tuple:
    """
    Toggle sudo mode for the current session (superusers only).

    While enabled, the flag is passed through to the UDM Rego policies as
    ``input.user.sudo``. Returns 403 for non-superusers.
    """
    if not request.user or not request.user.is_authenticated:
        return 401, ErrorOut(code="auth.notAuthenticated")
    if not request.user.is_superuser:
        return 403, ErrorOut(code="auth.permissionDenied")

    if payload.enabled:
        request.session[SUDO_SESSION_KEY] = True
    else:
        request.session.pop(SUDO_SESSION_KEY, None)
    logger.info("sudo mode %s for user %s", "enabled" if payload.enabled else "disabled", request.user.username)
    return 200, SudoModeOut(sudo_mode=payload.enabled)


@router.post(
    "/permission",
    response={200: None, 401: ErrorOut, 400: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
def get_current_user_object_permissions(request: HttpRequest, permission:PermissionIn) -> tuple:
    """
    Check the current authenticated user's permissions on specific objects.

    Returns 401 if user is not authenticated.
    """
    if not request.user or not request.user.is_authenticated:
        return 401, ErrorOut(code="auth.notAuthenticated")

    try:
        if not isinstance(request.user, OpenIDUser):
            return 401, ErrorOut(code="auth.notAuthenticated")
        user : OpenIDUser = request.user
        objtypename = permission.object_type.lower()
        appname = permission.app.lower()
        action = permission.action.lower()
        if not action.replace("_","").isalpha():
            return 400, ErrorOut(code="common.invalidRequest")
        if not objtypename.isalpha():
            return 400, ErrorOut(code="common.invalidRequest")
        # Dynamically get the model class based on the object type
        if appname not in apps.app_configs:
            return 400, ErrorOut(code="common.invalidRequest")
        objtype = apps.all_models[appname].get(objtypename, None)
        if not objtype:
            return 400, ErrorOut(code="common.invalidRequest")
        if not issubclass(objtype, Model):
            return 400, ErrorOut(code="common.invalidRequest")
        permname = f"{appname}.{action}_{objtypename}"
        objs : objtype = objtype.objects.filter(pk=permission.object_id).only()
        if len (objs) == 0:
            return 403, ErrorOut(code="auth.permissionDenied", detail=permname)
        obj = objs[0]
        if user.has_perm(permname, obj):
            return 200, None
        else:
            return 403, ErrorOut(code="auth.permissionDenied", detail=permname)

    except Exception as e:
        logger.error(f"Failed to retrieve user permissions: {str(e)}")
        raise Exception("Failed to retrieve permissions") from e


@router.get("/users", response={200: list[OpenIDUserOut], 401: ErrorOut, 403: ErrorOut})
@api_permission_required((openid_user_management, "view", OpenIDUser))
def list_users(
    request: HttpRequest, active_only: bool = True, limit: int = 100
) -> list:
    """
    List all users (or active users only).

    Query parameters:
    - active_only: If True, only return active users (default: True)
    - limit: Maximum number of users to return (default: 100)
    """
    queryset = OpenIDUser.objects.all()

    if active_only:
        queryset = queryset.filter(is_active=True)

    users = queryset.order_by("-date_joined")[:limit]
    return [_user_to_out(user) for user in users]


# Helper function
def _user_to_out(user: OpenIDUser) -> OpenIDUserOut:
    """Convert OpenIDUser model instance to OpenIDUserOut schema."""
    return OpenIDUserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        phone_number=user.phone_number,
        picture=user.picture,
        locale=user.locale,
        is_active=user.is_active,
        is_staff=user.is_staff,
        date_joined=user.date_joined,
        last_login=user.last_login,
        openid_subject=user.openid_subject,
        openid_provider=user.openid_provider,
    )

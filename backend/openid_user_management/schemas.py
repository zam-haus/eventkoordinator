"""
Pydantic schemas for OpenID User Management API.

Provides serialization/deserialization of OpenIDUser model for API endpoints.
"""

from typing import Optional
from uuid import UUID
from datetime import datetime
from ninja import Schema, Field
from apiv1.schemas import ErrorOut


class OpenIDUserOut(Schema):
    """Schema for serializing OpenIDUser in API responses."""

    id: UUID
    username: str
    email: str
    phone_number: Optional[str] = None
    picture: Optional[str] = None
    locale: Optional[str] = None
    is_active: bool
    is_staff: bool
    date_joined: datetime
    last_login: Optional[datetime] = None
    openid_subject: Optional[str] = None
    openid_provider: Optional[str] = None


class OpenIDUserCreate(Schema):
    """Schema for creating new OpenID users."""

    username: str
    email: str
    password: Optional[str] = None
    phone_number: Optional[str] = None
    picture: Optional[str] = None
    locale: Optional[str] = None
    openid_subject: Optional[str] = None
    openid_provider: Optional[str] = None


class OpenIDUserUpdate(Schema):
    """Schema for updating OpenID users."""

    username: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    picture: Optional[str] = None
    locale: Optional[str] = None
    is_active: Optional[bool] = None
    openid_subject: Optional[str] = None
    openid_provider: Optional[str] = None


class OpenIDUserLogin(Schema):
    """Schema for user login requests."""

    username: str
    password: str


class OpenIDUserProfile(Schema):
    """Schema for user profile information."""

    id: UUID
    username: str
    email: str
    full_name: str
    short_name: str
    picture: Optional[str] = None
    locale: Optional[str] = None
    phone_number: Optional[str] = None


class PermissionsOut(Schema):
    """Schema for user permissions response."""

    is_authenticated: bool
    is_staff: bool
    is_superuser: bool
    is_active: bool
    sudo_mode: bool = False
    permissions: list[str]


class SudoModeIn(Schema):
    """Schema for toggling sudo mode on the current session."""

    enabled: bool


class SudoModeOut(Schema):
    """Schema for the sudo mode state of the current session."""

    sudo_mode: bool


class PermissionIn(Schema):
    """Schema for user permissions request."""

    app: str = Field(description="The app label of the permission, e.g. 'auth' or 'myapp'. Uppercase or lowercase is fine.")
    action: str = Field(description="The action of the permission, e.g. 'view', 'add', 'change', 'delete'. Uppercase or lowercase is fine.")
    object_type: str = Field(description="The type of object that this permission is for. Uppercase or lowercase is fine.")
    object_id: Optional[str] = None
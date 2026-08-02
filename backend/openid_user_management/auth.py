"""
Custom OIDC Authentication Backend for OpenIDUser model.

Integrates mozilla-django-oidc with our custom UUID-based user model.
"""

import logging

from django.conf import settings
from django.http import HttpRequest
from django.template.defaultfilters import urlencode
from mozilla_django_oidc.auth import OIDCAuthenticationBackend as BaseOIDCAuthenticationBackend
from openid_user_management.models import OpenIDUser
from django.core.exceptions import SuspiciousOperation

logger = logging.getLogger(__name__)


class OIDCAuthenticationBackend(BaseOIDCAuthenticationBackend):
    """
    Custom OIDC authentication backend for OpenIDUser model.

    Handles user creation and updates based on OIDC claims.
    """

    def create_user(self, claims):
        """
        Create a new OpenIDUser from OIDC claims.

        If the email from claims is already in use by an *unlinked* account
        (one with no ``openid_subject`` — e.g. a pre-OIDC account that has not
        yet logged in via OIDC) and the IdP asserts the email is verified, the
        existing account is claimed: its ``openid_subject``/provider and profile
        fields are populated and the existing user is returned. This preserves
        the legacy "first OIDC login links the matching local account" path
        without allowing account takeover.

        An email already in use by an account that *is* already linked to a
        different ``sub``, or an unverified email, is rejected with
        ``SuspiciousOperation`` — email is not a stable identifier and must not
        be used to bind a second identity to an existing account.

        Args:
            claims (dict): OIDC claims from the identity provider

        Returns:
            OpenIDUser: The created (or claimed) user instance
        """
        email = claims.get('email', '')
        sub = claims.get('sub', '')

        # Email collision handling: only an unlinked account may be claimed,
        # and only when the IdP confirms the email is verified.
        if email:
            existing = OpenIDUser.objects.filter(email=email).first()
            if existing is not None:
                if existing.openid_subject:
                    # Account is already linked to a different identity. Refuse
                    # to bind a second sub to it via email — that would be an
                    # account takeover via a reused/changed email.
                    logger.warning(
                        "Refusing OIDC user creation: email %s already bound to "
                        "linked account %s (sub=%s); new sub=%s",
                        email, existing.pk, existing.openid_subject, sub,
                    )
                    raise SuspiciousOperation(
                        "Email already linked to another OpenID subject."
                    )
                if not claims.get('email_verified'):
                    logger.warning(
                        "Refusing OIDC user creation: email %s is unverified "
                        "and already in use by unlinked account %s",
                        email, existing.pk,
                    )
                    raise SuspiciousOperation(
                        "Email already in use and not verified by the identity "
                        "provider."
                    )
                # Claim the unlinked pre-OIDC account for this identity.
                logger.warning(
                    "Claiming unlinked account %s (email=%s) for new OIDC "
                    "subject %s from provider %s",
                    existing.pk, email, sub, self.get_provider_name(claims),
                )
                return self.update_user(existing, claims)

        username = claims.get('preferred_username', email.split('@')[0] if email else '')

        # Generate unique username if it already exists
        base_username = username
        counter = 1
        while OpenIDUser.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1

        user = OpenIDUser.objects.create_user(
            username=username,
            email=email,
            password=None,  # No password for OIDC users
        )

        # Store OIDC provider information
        user.openid_subject = sub
        user.openid_provider = self.get_provider_name(claims)

        # Store optional profile information
        user.phone_number = claims.get('phone_number', '')
        user.picture = claims.get('picture', '')
        user.locale = claims.get('locale', '')

        user.save()

        logger.info(f"Created new OIDC user: {user.username} (UUID: {user.id})")
        return user

    def update_user(self, user, claims):
        """
        Update existing user with latest OIDC claims.

        Args:
            user (OpenIDUser): The user to update
            claims (dict): OIDC claims from the identity provider

        Returns:
            OpenIDUser: The updated user instance
        """
        # Update email if changed
        email = claims.get('email', '')
        if email and email != user.email:
            user.email = email

        # Update OIDC subject if not set
        if not user.openid_subject:
            user.openid_subject = claims.get('sub', '')

        # Update provider if not set
        if not user.openid_provider:
            user.openid_provider = self.get_provider_name(claims)

        # Update optional profile information
        if 'phone_number' in claims:
            user.phone_number = claims.get('phone_number', '')
        if 'picture' in claims:
            user.picture = claims.get('picture', '')
        if 'locale' in claims:
            user.locale = claims.get('locale', '')

        user.save()

        logger.debug(f"Updated OIDC user: {user.username}")
        return user

    def filter_users_by_claims(self, claims):
        """
        Find users matching the OIDC claims.

        Matching is done **only** by the ``sub`` (subject) claim, which is the
        stable, issuer-assigned identifier. Email is deliberately **not** used
        as a fallback: email is not guaranteed unique across identities, is not
        immutable, and may be unverified, so matching an existing account by
        email would allow an attacker who controls a second identity with a
        matching email to bind to and take over an existing account.

        Args:
            claims (dict): OIDC claims from the identity provider

        Returns:
            QuerySet: Users matching the ``sub`` claim (empty if absent)
        """
        sub = claims.get('sub')
        if not sub:
            return OpenIDUser.objects.none()
        return OpenIDUser.objects.filter(openid_subject=sub)

    def get_provider_name(self, claims):
        """
        Extract provider name from claims.

        Args:
            claims (dict): OIDC claims

        Returns:
            str: Provider name
        """
        # Try to get issuer
        issuer = claims.get('iss', '')

        # Extract provider name from issuer URL
        if 'google' in issuer.lower():
            return 'google'
        elif 'github' in issuer.lower():
            return 'github'
        elif 'keycloak' in issuer.lower():
            return 'keycloak'
        elif 'auth0' in issuer.lower():
            return 'auth0'
        else:
            return issuer.split('//')[1].split('/')[0] if '//' in issuer else issuer

    def verify_claims(self, claims):
        """
        Verify that required claims are present.

        Requires the ``sub`` claim: it is the stable, issuer-assigned subject
        identifier and the only value used to bind a login to an existing
        account (see ``filter_users_by_claims``). Email alone is not accepted,
        because an unverified or shared email must not authorize a login.

        Args:
            claims (dict): OIDC claims

        Returns:
            bool: True if a ``sub`` claim is present
        """
        return bool(claims.get('sub'))


def generate_username(email):
    """
    Generate a username from email address.

    This function is referenced in settings.OIDC_USERNAME_ALGO.

    Args:
        email (str): Email address

    Returns:
        str: Generated username
    """
    if not email:
        return f"user_{OpenIDUser.objects.count() + 1}"

    # Use email local part as username
    username = email.split('@')[0]

    # Make it unique if necessary
    base_username = username
    counter = 1
    while OpenIDUser.objects.filter(username=username).exists():
        username = f"{base_username}{counter}"
        counter += 1

    return username



def provider_logout(request: HttpRequest):
    keycloak_logout_url = settings.OIDC_OP_LOGOUT_URL
    client_id = settings.OIDC_RP_CLIENT_ID
    redirect_url = request.build_absolute_uri(settings.LOGOUT_REDIRECT_URL)
    return_url = keycloak_logout_url.format(urlencode(redirect_url), urlencode(client_id))
    return return_url
"""
Tests for OIDC Authentication Backend.

Tests the custom OIDCAuthenticationBackend integration with OpenIDUser model.
"""

from django.test import TestCase
from unittest.mock import Mock, patch
from openid_user_management.models import OpenIDUser
from openid_user_management.auth import OIDCAuthenticationBackend, generate_username, SuspiciousOperation


class OIDCAuthenticationBackendTests(TestCase):
    """Tests for the custom OIDC authentication backend."""

    def setUp(self):
        """Set up test fixtures."""
        self.backend = OIDCAuthenticationBackend()
        self.sample_claims = {
            'sub': 'google_12345',
            'email': 'test@example.com',
            'preferred_username': 'testuser',
            'picture': 'https://example.com/pic.jpg',
            'locale': 'en-US',
            'phone_number': '+1234567890',
            'iss': 'https://accounts.google.com',
        }

    def test_create_user_from_claims(self):
        """Test that a user is created from OIDC claims."""
        user = self.backend.create_user(self.sample_claims)

        self.assertIsNotNone(user)
        self.assertEqual(user.email, 'test@example.com')
        self.assertEqual(user.username, 'testuser')
        self.assertEqual(user.openid_subject, 'google_12345')
        self.assertEqual(user.openid_provider, 'google')
        self.assertEqual(user.picture, 'https://example.com/pic.jpg')
        self.assertEqual(user.locale, 'en-US')
        self.assertEqual(user.phone_number, '+1234567890')

    def test_create_user_unique_username(self):
        """Test that duplicate usernames are handled."""
        # Create first user
        user1 = self.backend.create_user(self.sample_claims)
        self.assertEqual(user1.username, 'testuser')

        # Create second user with same preferred_username
        claims2 = self.sample_claims.copy()
        claims2['sub'] = 'google_67890'
        claims2['email'] = 'test2@example.com'

        user2 = self.backend.create_user(claims2)
        self.assertEqual(user2.username, 'testuser1')  # Auto-incremented

    def test_update_user(self):
        """Test that user is updated with new claims."""
        user = self.backend.create_user(self.sample_claims)

        # Update claims
        new_claims = self.sample_claims.copy()
        new_claims['email'] = 'newemail@example.com'
        new_claims['picture'] = 'https://example.com/newpic.jpg'

        updated_user = self.backend.update_user(user, new_claims)

        self.assertEqual(updated_user.email, 'newemail@example.com')
        self.assertEqual(updated_user.picture, 'https://example.com/newpic.jpg')

    def test_filter_users_by_sub(self):
        """Test finding users by OpenID subject."""
        user = self.backend.create_user(self.sample_claims)

        found_users = self.backend.filter_users_by_claims(self.sample_claims)

        self.assertEqual(found_users.count(), 1)
        self.assertEqual(found_users.first().id, user.id)

    def test_filter_users_by_email_no_longer_matches(self):
        """Email must not be used to match an existing account — only ``sub``.

        A different ``sub`` with the same email as an existing user returns
        *no* users, so the caller cannot bind a second identity to the
        existing account via a reused/changed email.
        """
        user = self.backend.create_user(self.sample_claims)

        # Search with a different sub but the same email.
        claims = {
            'sub': 'different_sub',
            'email': 'test@example.com'
        }

        found_users = self.backend.filter_users_by_claims(claims)

        self.assertEqual(found_users.count(), 0)

    def test_filter_users_without_sub_returns_none(self):
        """Claims without a ``sub`` match no users."""
        self.backend.create_user(self.sample_claims)

        found_users = self.backend.filter_users_by_claims({'email': 'test@example.com'})

        self.assertEqual(found_users.count(), 0)

    def test_get_provider_name_google(self):
        """Test provider name extraction for Google."""
        claims = {'iss': 'https://accounts.google.com'}
        provider = self.backend.get_provider_name(claims)
        self.assertEqual(provider, 'google')

    def test_get_provider_name_keycloak(self):
        """Test provider name extraction for Keycloak."""
        claims = {'iss': 'http://localhost:8080/realms/keycloak'}
        provider = self.backend.get_provider_name(claims)
        self.assertEqual(provider, 'keycloak')

    def test_get_provider_name_generic(self):
        """Test provider name extraction for generic provider."""
        claims = {'iss': 'https://auth.example.com'}
        provider = self.backend.get_provider_name(claims)
        self.assertEqual(provider, 'auth.example.com')

    def test_verify_claims_with_sub(self):
        """Test claim verification with sub."""
        claims = {'sub': 'user123'}
        self.assertTrue(self.backend.verify_claims(claims))

    def test_verify_claims_with_email_only_fails(self):
        """Email without a ``sub`` must not pass claim verification, because
        email is not used to bind a login to an existing account."""
        claims = {'email': 'user@example.com'}
        self.assertFalse(self.backend.verify_claims(claims))

    def test_verify_claims_missing(self):
        """Test claim verification fails when required claims missing."""
        claims = {}
        self.assertFalse(self.backend.verify_claims(claims))

    def test_generate_username_from_email(self):
        """Test username generation from email."""
        username = generate_username('john.doe@example.com')
        self.assertEqual(username, 'john.doe')

    def test_generate_username_unique(self):
        """Test that generated usernames are unique."""
        # Create user with username 'john'
        OpenIDUser.objects.create_user(
            username='john',
            email='john@example.com',
            password='test123'
        )

        # Generate username for same email pattern
        username = generate_username('john@different.com')
        self.assertEqual(username, 'john1')

    def test_create_user_without_password(self):
        """Test that OIDC users are created without password."""
        user = self.backend.create_user(self.sample_claims)

        # User should not be able to login with password
        self.assertFalse(user.has_usable_password())

    def test_create_user_minimal_claims(self):
        """Test user creation with minimal claims."""
        minimal_claims = {
            'sub': 'user123',
            'email': 'minimal@example.com'
        }

        user = self.backend.create_user(minimal_claims)

        self.assertIsNotNone(user)
        self.assertEqual(user.email, 'minimal@example.com')
        self.assertEqual(user.openid_subject, 'user123')
        self.assertEqual(user.username, 'minimal')  # From email

    def test_update_user_preserves_existing_data(self):
        """Test that update doesn't overwrite data if not in claims."""
        user = self.backend.create_user(self.sample_claims)
        original_picture = user.picture

        # Update with claims that don't include picture
        claims_without_picture = {
            'sub': 'google_12345',
            'email': 'test@example.com',
            'locale': 'fr-FR'
        }

        updated_user = self.backend.update_user(user, claims_without_picture)

        # Picture should be preserved
        self.assertEqual(updated_user.picture, original_picture)
        # Locale should be updated
        self.assertEqual(updated_user.locale, 'fr-FR')

    # ── Email-collision / account-claiming security tests ─────────────────────

    def test_create_user_claims_unlinked_account(self):
        """A verified email matching an *unlinked* (no ``openid_subject``)
        pre-OIDC account claims that account instead of creating a new one.
        """
        # Pre-existing local account with no openid_subject.
        existing = OpenIDUser.objects.create_user(
            username='localuser', email='taken@example.com', password='x'
        )
        self.assertFalse(existing.openid_subject)

        claims = {
            'sub': 'new_sub_1',
            'email': 'taken@example.com',
            'email_verified': True,
            'preferred_username': 'localuser',
            'iss': 'https://accounts.google.com',
        }

        claimed = self.backend.create_user(claims)

        # The existing account is returned (claimed), not a new one.
        self.assertEqual(claimed.pk, existing.pk)
        self.assertEqual(claimed.openid_subject, 'new_sub_1')
        self.assertEqual(claimed.openid_provider, 'google')
        # No duplicate user was created.
        self.assertEqual(OpenIDUser.objects.filter(email='taken@example.com').count(), 1)

    def test_create_user_email_collision_already_linked_rejected(self):
        """An email already bound to a linked account (has a ``sub``) cannot be
        claimed by a different ``sub`` — that would be account takeover via a
        reused/changed email.
        """
        # Existing account already linked to an identity.
        self.backend.create_user({**self.sample_claims, 'email_verified': True})

        # A second identity reuses the same email with a different sub.
        attacker_claims = {
            'sub': 'attacker_sub',
            'email': 'test@example.com',
            'email_verified': True,
            'iss': 'https://accounts.google.com',
        }

        with self.assertRaises(SuspiciousOperation):
            self.backend.create_user(attacker_claims)

        # The original account's binding is unchanged.
        original = OpenIDUser.objects.get(email='test@example.com')
        self.assertEqual(original.openid_subject, 'google_12345')

    def test_create_user_email_collision_unverified_rejected(self):
        """An email matching an unlinked account but not marked verified by the
        IdP must not be auto-linked.
        """
        OpenIDUser.objects.create_user(
            username='localuser', email='taken@example.com', password='x'
        )

        unverified_claims = {
            'sub': 'new_sub_2',
            'email': 'taken@example.com',
            'email_verified': False,
            'iss': 'https://accounts.google.com',
        }

        with self.assertRaises(SuspiciousOperation):
            self.backend.create_user(unverified_claims)

        # The unlinked account is untouched.
        existing = OpenIDUser.objects.get(email='taken@example.com')
        self.assertFalse(existing.openid_subject)


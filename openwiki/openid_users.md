---
type: openid_documentation
title: OpenID User Management
description: Documentation for OpenID User Management
---

# OpenID User Management

The OpenID User Management system handles user authentication and authorization.

## Overview

OpenID User Management provides user authentication and authorization using OpenID Connect.

## Architecture

### User Model

The OpenIDUser model extends Django's AbstractUser with additional fields:

- **id**: UUID (primary key)
- **username**: String (unique)
- **email**: Email (unique)
- **phone_number**: String (optional)
- **picture**: URL (optional)
- **is_active**: Boolean
- **is_staff**: Boolean
- **is_superuser**: Boolean
- **groups**: Many-to-Many with Group
- **user_permissions**: Many-to-Many with Permission

### User Groups

Groups are used to organize users and assign permissions:

- **id**: UUID (primary key)
- **name**: String (unique)
- **permissions**: Many-to-Many with Permission

### User Permissions

Permissions define what users can do:

- **id**: UUID (primary key)
- **name**: String
- **codename**: String
- **content_type**: ForeignKey to ContentType

## Authentication

### OpenID Connect

The system uses OpenID Connect for authentication.

#### Configuration

```python
OIDC RP = {
    'OIDC_RP_CLIENT_ID': 'client_id',
    'OIDC_RP_CLIENT_SECRET': 'client_secret',
    'OIDC_RP_SCOPES': 'openid profile email',
    'OIDC_OP_ISSUER': 'https://auth.example.com',
    'OIDC_OP_AUTHORIZATION_ENDPOINT': 'https://auth.example.com/authorize',
    'OIDC_OP_TOKEN_ENDPOINT': 'https://auth.example.com/token',
    'OIDC_OP_USER_ENDPOINT': 'https://auth.example.com/userinfo',
}
```

#### Authentication Flow

1. User redirects to authorization endpoint
2. User authenticates with OpenID provider
3. Provider redirects to callback endpoint
4. Token exchange occurs
5. User information is retrieved
6. User is logged in or created

## API Endpoints

### User Management

#### Get User

`GET /api/udm/users/{user_id}/`

Retrieves a user by ID.

#### Get User by Email

`GET /api/udm/users/email/{email}/`

Retrieves a user by email address.

#### Get Current User

`GET /api/udm/me/`

Retrieves the current authenticated user's profile.

#### Update User

`PATCH /api/udm/users/{user_id}/`

Updates a user's information.

### Group Management

#### List Groups

`GET /api/udm/groups/`

Lists all user groups.

#### Create Group

`POST /api/udm/groups/`

Creates a new group.

#### Update Group

`PATCH /api/udm/groups/{group_id}/`

Updates a group.

### Permission Management

#### List Permissions

`GET /api/udm/permissions/`

Lists all permissions.

#### Assign Permission

`POST /api/udm/users/{user_id}/permissions/`

Assigns a permission to a user.

#### Remove Permission

`DELETE /api/udm/users/{user_id}/permissions/{permission_id}/`

Removes a permission from a user.

### Sudo Mode

#### Enable Sudo

`POST /api/udm/sudo/`

Enables sudo mode (requires password verification).

#### Disable Sudo

`POST /api/udm/sudo/exit/`

Disables sudo mode.

#### Sudo Status

`GET /api/udm/sudo/status/`

Checks if sudo mode is active.

## Security

### Password Requirements

- Minimum length: 12 characters
- Require uppercase, lowercase, numbers, special characters
- Password history (prevent reuse)
- Password expiration (90 days)

### Session Security

- Session timeout (30 minutes)
- Session regeneration
- Secure session storage

### Authentication Security

- Secure token storage
- Token expiration
- Token refresh
- Revocation support

## Permissions and Authorization

### Object-Level Permissions

- Per-user permissions on entities
- Group-based permissions
- Staff-only permissions

### Policy-Based Authorization

- Rego policies for fine-grained control
- Permission evaluation
- Action authorization

## Testing

### Unit Tests

```python
class OpenIDUserTests(TestCase):
    def test_user_creation(self):
        user = OpenIDUser.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123"
        )
        self.assertEqual(user.username, "testuser")
        self.assertTrue(user.check_password("testpass123"))
    
    def test_group_assignment(self):
        group = Group.objects.create(name="test_group")
        user = OpenIDUser.objects.create(username="testuser")
        group.openid_users.add(user)
        self.assertIn(user, group.openid_users.all())
```

### Integration Tests

```python
class OpenIDIntegrationTests(TestCase):
    def test_login_flow(self):
        # Test OpenID login flow
        response = self.client.post("/api/udm/login/", {
            "username": "testuser",
            "password": "testpass123"
        })
        self.assertEqual(response.status_code, 200)
```

## Best Practices

### User Management

1. **Security**: Always hash passwords
2. **Validation**: Validate email and username
3. **Audit**: Log user changes
4. **Privacy**: Handle user data carefully

### Permission Management

1. **Least Privilege**: Grant minimum required permissions
2. **Group-Based**: Use groups for permission management
3. **Review**: Regular permission reviews
4. **Logging**: Log permission changes

## Troubleshooting

### Common Issues

1. **Authentication Failure**
   - Check credentials
   - Verify token
   - Review logs

2. **Permission Denied**
   - Check user permissions
   - Verify group membership
   - Review policy rules

3. **Sudo Mode Issues**
   - Verify password
   - Check session
   - Review sudo configuration

## Future Enhancements

### Planned Features

1. **MFA Support**: Multi-factor authentication
2. **OAuth2 Support**: Support OAuth2 providers
3. **LDAP Integration**: LDAP authentication
4. **SSO Integration**: Single sign-on support

---
type: testing_documentation
title: Backend Tests
description: Documentation for backend tests
---

# Backend Tests

Backend tests cover the Django API, models, policies, and business logic.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Testing Overview](overview.md) - Testing strategy and approach
- [Backend Overview](../backend/overview.md) - Backend components

## Test Framework

### pytest

Backend tests use `pytest` as the test framework.

**Configuration**: `pyproject.toml`

```toml
[tool.pytest.ini_options]
testpaths = ["backend/apiv1/tests", "backend/userdefinedmodel/tests"]
python_files = "test_*.py"
addopts = "-v --cov=. --cov-report=html --cov-report=term-missing"
```

## Test Organization

### apiv1/tests

Tests for the `apiv1` app.

**Test Categories**:
- Series tests
- Proposals tests
- Reviews tests
- Sync tests
- Calendar tests

### userdefinedmodel/tests

Tests for the `userdefinedmodel` app.

**Test Categories**:
- API tests
- Policy tests
- Actions tests
- Workflow tests
- Migration tests

### openid_user_management/tests

Tests for the `openid_user_management` app.

**Test Categories**:
- User management tests
- Authentication tests
- Permission tests
- Group tests

### sync_*_tests

Tests for sync targets.

**Test Categories**:
- Pretix tests
- iCal tests
- CalDAV tests

## Test Patterns

### 1. API Tests

```python
class APIEndpointTests(TestCase):
    def test_endpoint_success(self):
        # Arrange
        user = StaffUserFactory()
        self.client.force_login(user)
        
        # Act
        response = self.client.get("/api/endpoint/")
        
        # Assert
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 10)
    
    def test_endpoint_unauthorized(self):
        # Act
        response = self.client.get("/api/endpoint/")
        
        # Assert
        self.assertEqual(response.status_code, 401)
```

### 2. Model Tests

```python
class ModelTests(TestCase):
    def test_model_creation(self):
        # Arrange
        config = FieldConfigFactory()
        
        # Act
        version = ConfigVersion.objects.create(
            config=config,
            version_name="v1"
        )
        
        # Assert
        self.assertIsNotNone(version.id)
        self.assertEqual(version.version_name, "v1")
    
    def test_model_validation(self):
        # Act & Assert
        with self.assertRaises(ValidationError):
            InvalidModel.objects.create()
```

### 3. Policy Tests

```python
class PolicyTests(TestCase):
    def test_policy_evaluation(self):
        # Arrange
        entity = EntityFactory()
        policy = PolicyFactory()
        
        # Act
        result = evaluate_policy(entity, request.user, "view")
        
        # Assert
        self.assertTrue(result.allow)
        self.assertEqual(len(result.messages), 0)
```

### 4. Workflow Tests

```python
class WorkflowTests(TestCase):
    def test_workflow_transition(self):
        # Arrange
        entity = EntityFactory()
        workflow = WorkflowFactory()
        
        # Act
        result = transition_workflow(entity, "transition_name")
        
        # Assert
        self.assertTrue(result.success)
        self.assertEqual(entity.status, "new_status")
```

## Test Fixtures

### Factory Definitions

```python
class UserFactory(DjangoModelFactory):
    class Meta:
        model = "openid_user_management.OpenIDUser"
    
    username = faker.user_name()
    email = faker.email()
    is_active = True
    is_staff = False
```

### Test Data

- `factories.py`: Factory definitions
- `test_data/`: Test data files
- `fixtures/`: JSON fixtures

## Coverage Targets

- Backend: 80%+ coverage
- Frontend: 70%+ coverage

## Running Tests

### Run All Tests

```bash
pytest backend/ -v
```

### Run Specific Test File

```bash
pytest backend/apiv1/tests/test_api.py -v
```

### Run Specific Test

```bash
pytest backend/apiv1/tests/test_api.py::EntityTests::test_create_entity -v
```

### Run Tests with Coverage

```bash
pytest backend/ -v --cov=. --cov-report=html
```

## Test Data Management

### Backend Test Data

- **Factories**: Factory definitions for test data
- **Fixtures**: Predefined test data
- **Migrations**: Test migrations

## Performance Testing

### Backend Performance

- Response time benchmarks
- Memory usage benchmarks
- Database query benchmarks

## Security Testing

### Security Tests

- XSS prevention
- CSRF protection
- Authentication
- Authorization

## Documentation

### Test Documentation

- Test cases documented
- Test results tracked
- Coverage reports published

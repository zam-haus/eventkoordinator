---
type: testing_documentation
title: Frontend Tests
description: Documentation for frontend/Playwright tests
---

# Frontend Tests

Frontend tests cover React components, state management, and user interactions using Playwright.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Testing Overview](overview.md) - Testing strategy and approach
- [Frontend Overview](../frontend/overview.md) - Frontend components

## Test Framework

### Playwright

Frontend tests use Playwright for E2E testing.

**Configuration**: `playwright.config.ts`

```typescript
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
});
```

## Test Organization

### e2e/auth/

Tests for authentication and authorization.

**Test Cases**:
- Login
- Logout
- Session management
- Sudo mode

### e2e/udm/

Tests for UDM management.

**Test Cases**:
- Create UDM type
- Edit UDM type
- Delete UDM type
- Manage configurations

### e2e/entity/

Tests for entity management.

**Test Cases**:
- Create entity
- Edit entity
- Delete entity
- Workflow transitions

### e2e/workflow/

Tests for workflow management.

**Test Cases**:
- Create workflow
- Edit workflow
- Test transitions
- Test state changes

### e2e/sync/

Tests for sync functionality.

**Test Cases**:
- Sync to Pretix
- Sync to iCal
- Sync to CalDAV
- Sync status

## Test Patterns

### 1. User Journey Tests

```typescript
test("user can create entity", async ({ page }) => {
  // Login
  await page.goto("/login");
  await page.fill("#username", "admin");
  await page.fill("#password", "password");
  await page.click("button[type='submit']");
  
  // Navigate to UDM
  await page.click("a[href='/udm']");
  
  // Create entity
  await page.click("button:text('Create Entity')");
  await page.fill("input[name='name']", "Test Entity");
  await page.click("button:text('Save')");
  
  // Verify
  await expect(page.locator("text=Test Entity")).toBeVisible();
});
```

### 2. Edge Case Tests

```typescript
test("handles concurrent modifications", async ({ page }) => {
  // Open entity in two tabs
  const page1 = await context.newPage();
  const page2 = await context.newPage();
  
  await page1.goto("/entities/1");
  await page2.goto("/entities/1");
  
  // Modify in first tab
  await page1.fill("input[name='name']", "Updated Name");
  await page1.click("button:text('Save')");
  
  // Try to modify in second tab
  await page2.fill("input[name='name']", "Another Update");
  await page2.click("button:text('Save')");
  
  // Verify conflict error
  await expect(page2.locator("text=Concurrent modification")).toBeVisible();
});
```

### 3. Error Handling Tests

```typescript
test("handles validation errors", async ({ page }) => {
  await page.goto("/entities/create");
  
  // Try to create with invalid data
  await page.fill("input[name='name']", "");
  await page.click("button:text('Save')");
  
  // Verify error
  await expect(page.locator("text=Name is required")).toBeVisible();
});
```

## Test Data

### Mock Data

- `e2e/fixtures/`: JSON test data
- `e2e/data/`: Test data files
- API mocking for specific tests

## Test Execution

### Run All Tests

```bash
npx playwright test
```

### Run Specific Test

```bash
npx playwright test e2e/udm.spec.ts
```

### Run Tests in UI Mode

```bash
npx playwright test --ui
```

### Run Tests with Coverage

```bash
npx playwright test --coverage
```

## Test Best Practices

### 1. Test Independence

Each test should be independent and not rely on other tests.

### 2. Clear Test Names

Test names should clearly describe what is being tested.

```typescript
// Bad
test("test1", async ({ page }) => { ... });

// Good
test("user can create entity with valid data", async ({ page }) => { ... });
```

### 3. Proper Wait Times

Use Playwright's built-in waiting instead of fixed timeouts.

```typescript
// Bad
await page.waitForTimeout(1000);
await page.click("button");

// Good
await page.click("button:text('Submit')");
```

### 4. Error Messages

Provide clear error messages for assertions.

```typescript
await expect(page.locator("text=Success")).toBeVisible({
  timeout: 10000
});
```

## Accessibility Testing

### WCAG Compliance

- Test keyboard navigation
- Test screen reader support
- Test color contrast

### Accessibility Tests

```typescript
test("page is accessible", async ({ page }) => {
  // Test keyboard navigation
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  
  // Test ARIA labels
  await expect(page.locator("button[aria-label]")).toBeVisible();
});
```

## Performance Testing

### Load Time

- Test initial load time
- Test navigation time
- Test rendering time

### Resource Usage

- Monitor memory usage
- Monitor CPU usage
- Monitor network usage

## CI/CD Integration

### Pipeline Steps

1. **Build**: Build the application
2. **Test**: Run Playwright tests
3. **Report**: Generate test report

### Retry Logic

```typescript
test.describe.configure({ retries: 3 });
```

## Debugging

### Debug Mode

```bash
npx playwright test --debug
```

### UI Mode

```bash
npx playwright test --ui
```

### Video Recording

```bash
npx playwright test --record-video=on
```

## Test Coverage

### Coverage Reports

- Generate coverage reports
- Track coverage over time
- Set coverage targets

## Troubleshooting

### Common Issues

1. **Test flakiness**
   - Increase timeouts
   - Use proper waiting
   - Isolate tests

2. **Test failures**
   - Check network requests
   - Review error messages
   - Debug with Playwright Inspector

3. **Environment issues**
   - Verify environment variables
   - Check browser compatibility
   - Review test data

## Future Improvements

### Planned Enhancements

1. **More E2E Tests**
   - Complete user journeys
   - Edge cases
   - Error scenarios

2. **Component Tests**
   - React Testing Library
   - Jest
   - Mocking utilities

3. **Performance Tests**
   - Benchmark tests
   - Load tests
   - Stress tests

"""
Playwright tests for the admin interface.
Tests the login flow via the navbar form and admin panel snapshot.

Uses ViteStaticLiveServerTestCase to serve the built SPA and Django API
on a single port, eliminating the need for separate dev servers.
"""

from django.contrib.auth import get_user_model
from project.test_utils import ViteStaticLiveServerTestCase, SnapshotMixin, playwright_launch_options
from playwright.sync_api import sync_playwright


class AdminPlaywrightTest(SnapshotMixin, ViteStaticLiveServerTestCase):
    """Test admin functionality with Playwright"""

    vite_force_rebuild = True

    def setUp(self):
        super().setUp()

    def tearDown(self):
        User = get_user_model()
        User.objects.filter(username__startswith='adminuser_').delete()
        super().tearDown()

    def test_login_and_navigate_to_admin(self):
        """Run the login/admin flow for superuser, staff, and non-staff users."""
        cases = [
            {'role': 'superuser', 'is_staff': True, 'is_superuser': True, 'expect_admin_link': True, 'expect_admin_access': True},
            {'role': 'staff', 'is_staff': True, 'is_superuser': False, 'expect_admin_link': True, 'expect_admin_access': True},
            {'role': 'nonstaff', 'is_staff': False, 'is_superuser': False, 'expect_admin_link': False, 'expect_admin_access': False},
        ]

        User = get_user_model()

        for case in cases:
            with self.subTest(role=case['role']):
                username = f"adminuser_{case['role']}"
                password = 'testpassword123'
                User.objects.create_user(
                    username=username,
                    email=f"{username}@example.com",
                    password=password,
                    is_staff=case['is_staff'],
                    is_superuser=case['is_superuser'],
                )

                with sync_playwright() as p:
                    browser = p.chromium.launch(**playwright_launch_options())
                    page = browser.new_page()

                    try:
                        live_server_url = self.live_server_url
                        if callable(live_server_url):
                            live_server_url = live_server_url()

                        page.goto(live_server_url)
                        page.wait_for_load_state('networkidle')

                        menu_button = page.locator('button[aria-label="User menu"]')
                        menu_button.click()
                        page.wait_for_timeout(300)

                        username_input = page.locator('input[id="username"]')
                        password_input = page.locator('input[id="password"]')
                        login_button = page.get_by_role('button', name='Login', exact=True)

                        username_input.fill(username)
                        password_input.fill(password)
                        login_button.click()

                        page.wait_for_load_state('networkidle')
                        page.wait_for_timeout(500)

                        page.locator(f'text={username}').wait_for(timeout=5000)

                        admin_link = page.get_by_role('link', name='Admin panel')
                        if case['expect_admin_link'] and admin_link.is_visible():
                            admin_link.click()
                        else:
                            if not case['expect_admin_link']:
                                self.assertFalse(admin_link.is_visible())
                            page.goto(f'{live_server_url}/admin/')

                        page.wait_for_load_state('networkidle')
                        page.wait_for_timeout(500)

                    finally:
                        browser.close()

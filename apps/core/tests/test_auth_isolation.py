from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role

from .fixtures import (
    auth_client,
    full_school_setup,
    make_candidate,
    make_membership,
    make_user,
)


class AuthTests(TestCase):
    def setUp(self):
        self.setup = full_school_setup()
        self.admin = self.setup["admin"]

    def test_login_returns_tokens(self):
        response = APIClient().post(
            "/api/v1/auth/login/",
            {"email": self.admin.email, "password": "testpass123"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIn("access", data)
        self.assertIn("refresh", data)

    def test_login_rejects_bad_password(self):
        response = APIClient().post(
            "/api/v1/auth/login/",
            {"email": self.admin.email, "password": "wrong"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_me_requires_auth(self):
        self.assertEqual(APIClient().get("/api/v1/auth/me/").status_code, 401)

    def test_me_returns_user(self):
        client = auth_client(self.admin, self.setup["school"])
        response = client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["email"], self.admin.email)

    def test_change_password(self):
        client = auth_client(self.admin, self.setup["school"])
        response = client.post(
            "/api/v1/auth/change-password/",
            {
                "current_password": "testpass123",
                "new_password": "newpass456!",
                "confirm_password": "newpass456!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("newpass456!"))

    def test_logout_blacklists_refresh(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(self.admin)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        response = client.post("/api/v1/auth/logout/", {"refresh": str(refresh)}, format="json")
        self.assertEqual(response.status_code, 200)


class IsolationTests(TestCase):
    """School-scoped isolation and role enforcement."""

    def setUp(self):
        self.a = full_school_setup("AAA")
        self.b = full_school_setup("BBB")
        self.school_a = self.a["school"]
        self.school_b = self.b["school"]

    def test_school_b_cannot_list_school_a_candidates(self):
        make_candidate(self.school_a, "A-001")
        client = auth_client(self.b["admin"], self.school_b)
        response = client.get("/api/v1/candidates/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["count"], 0)

    def test_membership_spoofing_blocked(self):
        """School B admin can't see school A's data by header spoofing.

        Reads now span all of the user's own organizations — a foreign
        X-School-Id must not expose another organization's records."""
        make_candidate(self.school_a, "A-SPOOF")
        client = auth_client(self.b["admin"], self.school_a)
        response = client.get("/api/v1/candidates/")
        self.assertEqual(response.status_code, 200)
        numbers = [r["candidate_number"] for r in response.json()["data"]["results"]]
        self.assertNotIn("A-SPOOF", numbers)

    def test_superadmin_can_select_any_school(self):
        make_candidate(self.school_a, "A-002")
        superadmin = make_user("root@test.com", is_superadmin=True)
        client = auth_client(superadmin, self.school_a)
        response = client.get("/api/v1/candidates/")
        self.assertEqual(response.json()["data"]["count"], 1)

    def test_report_viewer_cannot_write(self):
        viewer = make_user("viewer@aaa.test")
        make_membership(viewer, self.school_a, Role.REPORT_VIEWER)
        client = auth_client(viewer, self.school_a)
        response = client.post(
            "/api/v1/candidates/",
            {"candidate_number": "X1", "first_name": "X", "last_name": "Y", "gender": "MALE"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_marks_entry_cannot_create_exam(self):
        user = make_user("entry@aaa.test")
        make_membership(user, self.school_a, Role.MARKS_ENTRY)
        client = auth_client(user, self.school_a)
        response = client.post(
            "/api/v1/examinations/", {"name": "E", "code": "E1"}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_denied(self):
        self.assertEqual(APIClient().get("/api/v1/candidates/").status_code, 401)

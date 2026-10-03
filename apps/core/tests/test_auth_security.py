"""Authentication & authorization tests: login, sessions, tokens, roles,
permissions, admin user management, security protections."""
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import (
    EmailVerificationToken,
    PasswordResetToken,
    Role,
    User,
    UserInvitation,
    UserSession,
)
from apps.accounts import services

from .fixtures import auth_client, make_membership, make_school, make_user

URL = "/api/v1"


class AuthFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("user@test.com")

    def test_register_creates_user(self):
        res = APIClient().post(
            f"{URL}/auth/register/",
            {"full_name": "John Peter", "email": "new@test.com",
             "phone": "+255700000000", "password": "Str0ng!Pass"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()["data"]
        self.assertIn("access", data)
        self.assertIn("refresh", data)
        user = User.objects.get(email="new@test.com")
        self.assertEqual(user.first_name, "John")
        self.assertEqual(user.last_name, "Peter")
        self.assertTrue(user.check_password("Str0ng!Pass"))
        self.assertEqual(user.status, User.Status.ACTIVE)

    def test_register_rejects_duplicate_email_case_insensitive(self):
        res = APIClient().post(
            f"{URL}/auth/register/",
            {"full_name": "A B", "email": "USER@TEST.COM", "password": "Str0ng!Pass"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["success"])

    def test_register_rejects_weak_password(self):
        res = APIClient().post(
            f"{URL}/auth/register/",
            {"full_name": "A B", "email": "weak@test.com", "password": "password"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_login_success_returns_roles_and_permissions(self):
        school = make_school("L1")
        make_membership(self.user, school, Role.EXAM_ADMIN)
        res = APIClient().post(
            f"{URL}/auth/login/",
            {"email": "user@test.com", "password": "testpass123"}, format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()["data"]
        self.assertIn("access", data)
        self.assertIn("refresh", data)
        self.assertIn("EXAM_ADMIN", data["user"]["roles"])
        self.assertIn("exams.view", data["user"]["permissions"])
        self.assertNotIn("users.create", data["user"]["permissions"])

    def test_login_wrong_password_generic_error(self):
        res = APIClient().post(
            f"{URL}/auth/login/",
            {"email": "user@test.com", "password": "wrongpass"}, format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(
            res.json()["error"]["message"], "Invalid email or password."
        )

    def test_login_unknown_email_same_error(self):
        res = APIClient().post(
            f"{URL}/auth/login/",
            {"email": "ghost@test.com", "password": "wrongpass"}, format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], "INVALID_CREDENTIALS")

    def test_suspended_user_cannot_login(self):
        self.user.status = User.Status.SUSPENDED
        self.user.save()
        res = APIClient().post(
            f"{URL}/auth/login/",
            {"email": "user@test.com", "password": "testpass123"}, format="json",
        )
        self.assertEqual(res.status_code, 400)
        # Generic message — account state is never revealed.
        self.assertEqual(res.json()["error"]["message"], "Invalid email or password.")

    @override_settings(LOGIN_MAX_FAILED_ATTEMPTS=3)
    def test_brute_force_lockout(self):
        client = APIClient()
        for _ in range(3):
            client.post(
                f"{URL}/auth/login/",
                {"email": "user@test.com", "password": "bad"}, format="json",
            )
        # Now even the correct password is temporarily blocked.
        res = client.post(
            f"{URL}/auth/login/",
            {"email": "user@test.com", "password": "testpass123"}, format="json",
        )
        self.assertEqual(res.json()["error"]["code"], "RATE_LIMITED")

    def test_me_includes_roles_permissions(self):
        school = make_school("L2")
        make_membership(self.user, school, Role.MARKS_ENTRY)
        res = auth_client(self.user, school).get(f"{URL}/auth/me/")
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]
        self.assertIn("MARKS_ENTRY", data["roles"])
        self.assertIn("marks.enter", data["permissions"])
        self.assertNotIn("users.create", data["permissions"])

    def test_profile_patch(self):
        res = auth_client(self.user).patch(
            f"{URL}/auth/profile/",
            {"first_name": "Updated", "phone": "+255711000000"}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.user.phone, "+255711000000")

    def test_change_password_mismatch(self):
        res = auth_client(self.user).post(
            f"{URL}/auth/change-password/",
            {"current_password": "testpass123", "new_password": "Xy9!zzzz",
             "confirm_password": "different"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_change_password_wrong_current(self):
        res = auth_client(self.user).post(
            f"{URL}/auth/change-password/",
            {"current_password": "nope", "new_password": "Xy9!zzzz",
             "confirm_password": "Xy9!zzzz"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)


class TokenSessionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("sess@test.com")

    def _login(self, ua="TestAgent/1.0"):
        res = APIClient().post(
            f"{URL}/auth/login/",
            {"email": "sess@test.com", "password": "testpass123"}, format="json",
            HTTP_USER_AGENT=ua,
        )
        return res.json()["data"]

    def test_login_creates_session(self):
        data = self._login()
        self.assertEqual(UserSession.objects.count(), 1)
        s = UserSession.objects.first()
        self.assertTrue(s.is_active)
        self.assertIn("session_id", data)

    def test_sessions_listed_and_revocable(self):
        self._login("Browser A")
        data2 = self._login("Browser B")
        self.assertEqual(UserSession.objects.count(), 2)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {data2['access']}")
        res = client.get(f"{URL}/auth/sessions/")
        self.assertEqual(len(res.json()["data"]), 2)
        other = UserSession.objects.exclude(refresh_jti=RefreshToken(data2["refresh"])["jti"]).first()
        res = client.post(f"{URL}/auth/sessions/{other.pk}/revoke/")
        self.assertEqual(res.status_code, 200)
        other.refresh_from_db()
        self.assertIsNotNone(other.revoked_at)

    def test_cannot_revoke_other_users_session(self):
        self._login()
        stranger = make_user("stranger@test.com")
        session = UserSession.objects.first()
        client = auth_client(stranger)
        res = client.post(f"{URL}/auth/sessions/{session.pk}/revoke/")
        self.assertIn(res.status_code, (404, 403))

    def test_logout_all_revokes_sessions(self):
        d1 = self._login()
        self._login()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {d1['access']}")
        res = client.post(f"{URL}/auth/logout-all/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            UserSession.objects.filter(revoked_at__isnull=False).count(), 2
        )

    def test_revoked_session_refresh_fails(self):
        data = self._login()
        session = UserSession.objects.first()
        services.revoke_session(session)
        res = APIClient().post(
            f"{URL}/auth/token/refresh/", {"refresh": data["refresh"]}, format="json"
        )
        self.assertEqual(res.status_code, 401)

    def test_logout_blacklists_refresh(self):
        data = self._login()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['access']}")
        res = client.post(f"{URL}/auth/logout/", {"refresh": data["refresh"]}, format="json")
        self.assertEqual(res.status_code, 200)
        res = APIClient().post(
            f"{URL}/auth/token/refresh/", {"refresh": data["refresh"]}, format="json"
        )
        self.assertEqual(res.status_code, 401)


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("reset@test.com")

    def test_forgot_password_generic_response(self):
        client = APIClient()
        for email in ("reset@test.com", "nobody@test.com"):
            res = client.post(
                f"{URL}/auth/forgot-password/", {"email": email}, format="json"
            )
            self.assertEqual(res.status_code, 200)
            self.assertIn("if an account exists", res.json()["message"].lower())
        # Only the real user got an email.
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("reset@test.com", mail.outbox[0].to[0])

    def _issue_reset(self):
        token, raw = PasswordResetToken.issue(self.user, timedelta(minutes=30))
        return token, raw

    def test_reset_password_flow(self):
        _, raw = self._issue_reset()
        res = APIClient().post(
            f"{URL}/auth/reset-password/",
            {"token": raw, "new_password": "Xy9!zzzz", "confirm_password": "Xy9!zzzz"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Xy9!zzzz"))

    def test_reset_token_single_use(self):
        _, raw = self._issue_reset()
        payload = {"token": raw, "new_password": "Xy9!zzzz", "confirm_password": "Xy9!zzzz"}
        self.assertEqual(APIClient().post(f"{URL}/auth/reset-password/", payload, format="json").status_code, 200)
        res = APIClient().post(f"{URL}/auth/reset-password/", payload, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], "RESET_TOKEN_INVALID")

    def test_expired_reset_token(self):
        token, raw = self._issue_reset()
        token.expires_at = timezone.now() - timedelta(minutes=1)
        token.save(update_fields=["expires_at"])
        res = APIClient().post(
            f"{URL}/auth/reset-password/",
            {"token": raw, "new_password": "Xy9!zzzz", "confirm_password": "Xy9!zzzz"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_reset_revokes_sessions(self):
        res = APIClient().post(
            f"{URL}/auth/login/",
            {"email": "reset@test.com", "password": "testpass123"}, format="json",
        )
        self.assertEqual(UserSession.objects.filter(revoked_at=None).count(), 1)
        _, raw = self._issue_reset()
        APIClient().post(
            f"{URL}/auth/reset-password/",
            {"token": raw, "new_password": "Xy9!zzzz", "confirm_password": "Xy9!zzzz"},
            format="json",
        )
        self.assertEqual(UserSession.objects.filter(revoked_at=None).count(), 0)


class EmailVerificationTests(TestCase):
    def setUp(self):
        self.user = make_user("verify@test.com")

    def test_verify_email(self):
        _, raw = EmailVerificationToken.issue(self.user, timedelta(hours=1))
        res = APIClient().post(f"{URL}/auth/verify-email/", {"token": raw}, format="json")
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_verify_token_single_use(self):
        _, raw = EmailVerificationToken.issue(self.user, timedelta(hours=1))
        APIClient().post(f"{URL}/auth/verify-email/", {"token": raw}, format="json")
        res = APIClient().post(f"{URL}/auth/verify-email/", {"token": raw}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_resend_verification_generic(self):
        client = APIClient()
        for email in ("verify@test.com", "ghost@test.com"):
            res = client.post(
                f"{URL}/auth/resend-verification/", {"email": email}, format="json"
            )
            self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)


class InvitationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = make_school("INV")
        self.admin = make_user("admin-inv@test.com", is_superadmin=True)

    def test_admin_invite_user(self):
        client = auth_client(self.admin)
        res = client.post(
            f"{URL}/users/",
            {"email": "invited@test.com", "first_name": "Inv", "last_name": "Ted",
             "role": Role.EXAM_ADMIN},
            format="json",
            HTTP_X_SCHOOL_ID=str(self.school.pk),
        )
        self.assertEqual(res.status_code, 201, res.content)
        user = User.objects.get(email="invited@test.com")
        self.assertEqual(user.status, User.Status.PENDING_VERIFICATION)
        self.assertTrue(UserInvitation.objects.filter(user=user).exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_pending_user_cannot_login(self):
        user, _ = services.create_invitation(
            email="pending@test.com", first_name="P", last_name="U",
            phone="", role=None, school=None, actor=self.admin,
        )
        res = APIClient().post(
            f"{URL}/auth/login/",
            {"email": "pending@test.com", "password": "x"}, format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_accept_invitation_activates_and_assigns_membership(self):
        user, invitation = services.create_invitation(
            email="accept@test.com", first_name="A", last_name="U",
            phone="", role=Role.MARKS_ENTRY, school=self.school, actor=self.admin,
        )
        # Recover the raw token by re-issuing: consume via a fresh issue.
        inv, raw = UserInvitation.issue(user, timedelta(hours=72), school=self.school, role=Role.MARKS_ENTRY)
        res = APIClient().post(
            f"{URL}/auth/accept-invitation/",
            {"token": raw, "new_password": "Xy9!zzzz", "confirm_password": "Xy9!zzzz"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.email_verified)
        self.assertTrue(
            user.memberships.filter(school=self.school, role=Role.MARKS_ENTRY).exists()
        )
        self.assertIn("access", res.json()["data"])


class AdminUserManagementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = make_school("ADM")
        self.admin = make_user("root@test.com", is_superadmin=True)
        self.user = make_user("managed@test.com")
        self.normal = make_user("normal@test.com")
        make_membership(self.normal, self.school, Role.MARKS_ENTRY)

    def test_roles_list(self):
        res = auth_client(self.admin).get(f"{URL}/roles/")
        self.assertEqual(res.status_code, 200)
        roles = {r["role"]: r for r in res.json()["data"]}
        self.assertIn("EXAM_ADMIN", roles)
        self.assertIn("results.publish", roles["EXAM_ADMIN"]["permissions"])

    def test_suspend_blocks_login(self):
        client = auth_client(self.admin)
        res = client.post(f"{URL}/users/{self.user.pk}/suspend/")
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        res = APIClient().post(
            f"{URL}/auth/login/",
            {"email": "managed@test.com", "password": "testpass123"}, format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_activate_restores_login(self):
        self.user.status = User.Status.SUSPENDED
        self.user.save()
        res = auth_client(self.admin).post(f"{URL}/users/{self.user.pk}/activate/")
        self.assertEqual(res.status_code, 200)
        res = APIClient().post(
            f"{URL}/auth/login/",
            {"email": "managed@test.com", "password": "testpass123"}, format="json",
        )
        self.assertEqual(res.status_code, 200)

    def test_assign_roles(self):
        res = auth_client(self.admin).post(
            f"{URL}/users/{self.user.pk}/roles/",
            {"roles": ["REPORT_VIEWER", "MARKS_ENTRY"]}, format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("REPORT_VIEWER", res.json()["data"]["roles"])

    def test_non_admin_cannot_manage_users(self):
        res = auth_client(self.normal, self.school).post(
            f"{URL}/users/{self.user.pk}/suspend/"
        )
        self.assertEqual(res.status_code, 403)

    def test_user_cannot_modify_own_roles(self):
        res = auth_client(self.normal, self.school).patch(
            f"{URL}/auth/profile/",
            {"roles": ["EXAM_ADMIN"], "status": "ACTIVE", "is_superadmin": True},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.normal.refresh_from_db()
        self.assertFalse(self.normal.is_superadmin)
        self.assertNotIn("EXAM_ADMIN", self.normal.effective_roles())

    def test_auth_audit_visible_to_admin(self):
        services.log_auth_event("LOGIN_SUCCESS", user=self.user)
        res = auth_client(self.admin).get(f"{URL}/auth/audit-log/")
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(res.json()["data"]["count"], 1)

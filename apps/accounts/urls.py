from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("users", views.UserViewSet, basename="user")
router.register("memberships", views.MembershipViewSet, basename="membership")
router.register("auth/sessions", views.SessionViewSet, basename="auth-session")
router.register("auth/audit-log", views.AuthAuditViewSet, basename="auth-audit")

urlpatterns = [
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/register/", views.RegisterView.as_view(), name="auth-register"),
    path("auth/token/refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/logout-all/", views.LogoutAllView.as_view(), name="auth-logout-all"),
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("auth/profile/", views.ProfileView.as_view(), name="auth-profile"),
    path("auth/change-password/", views.ChangePasswordView.as_view(), name="auth-change-password"),
    path("auth/forgot-password/", views.ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("auth/reset-password/", views.ResetPasswordView.as_view(), name="auth-reset-password"),
    path("auth/verify-email/", views.VerifyEmailView.as_view(), name="auth-verify-email"),
    path("auth/resend-verification/", views.ResendVerificationView.as_view(), name="auth-resend-verification"),
    path("auth/accept-invitation/", views.AcceptInvitationView.as_view(), name="auth-accept-invitation"),
    path("auth/organization/", views.ClaimOrganizationView.as_view(), name="auth-claim-organization"),
    path("auth/organizations/", views.CreateOrganizationView.as_view(), name="auth-create-organization"),
    path("roles/", views.RoleView.as_view(), name="roles-list"),
    path("", include(router.urls)),
]

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

api_v1 = [
    path("", include("apps.accounts.urls")),
    path("schools/", include("apps.schools.urls")),
    path("candidates/", include("apps.candidates.urls")),
    path("candidate-lists/", include("apps.candidate_lists.urls")),
    path("subjects/", include("apps.subjects.urls")),
    path("grading/", include("apps.grading.urls")),
    path("", include("apps.examinations.urls")),
    path("enrollments/", include("apps.enrollment.urls")),
    path("marks/", include("apps.marks.urls")),
    path("results/", include("apps.results.urls")),
    path("rankings/", include("apps.ranking.urls")),
    path("reports/", include("apps.reports.urls")),
    path("analytics/", include("apps.analytics.urls")),
    path("sms/", include("apps.sms.urls")),
    path("audit/", include("apps.audit.urls")),
    path("imports/", include("apps.imports.urls")),
    path("exports/", include("apps.exports.urls")),
    path("settings/", include("apps.settings.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

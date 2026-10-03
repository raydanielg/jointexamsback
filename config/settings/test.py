from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

if env("DATABASE_URL"):
    DATABASES["default"] = dj_database_url.config(default=env("DATABASE_URL"))
else:
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = ()
# Scoped throttles on auth/import views stay enabled but effectively
# unlimited in tests.
for _scope in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]:
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][_scope] = "10000/hour"

# EMAS — Examination Management & Assessment System

A production-grade Django REST backend focused **strictly** on examination
management: candidate lists → candidates → examinations → subjects/components
→ marks entry → automatic result/grade/position calculation → review →
finalization → publishing → reports → optional SMS notifications.

It is **not** a school-management system: no fees, payroll, attendance,
teachers, timetables, libraries, transport, or admissions.

## Repository layout

```
exams/
  backend/          ← this project (Django backend — the complete API)
    apps/ config/ manage.py requirements*.txt ...
  (frontend/        ← reserved for a future web/mobile client)
```

## Stack

- Python 3.12, Django 5.1, Django REST Framework
- PostgreSQL (production) / SQLite (tests)
- JWT auth (SimpleJWT + token blacklist)
- Celery + Redis for async jobs (imports, reports, result calculation, SMS)
- drf-spectacular OpenAPI 3 / Swagger
- ReportLab (PDF), openpyxl (Excel), CSV
- pytest / Django test runner

## Architecture

```
apps/
  accounts/        users, school memberships, roles, JWT auth, permissions
  schools/         slim examination-participant records
  candidates/      examination candidates + guardian contact (for SMS)
  candidate_lists/ reusable, school-owned groups of candidates
  subjects/        reusable subject catalogue
  examinations/    exam lifecycle state machine, exam subjects & components
  enrollment/      list-based enrollment → ExaminationCandidate
  marks/           mark records, statuses, change log, comments, validation
  grading/         configurable grading schemes, grade bands, divisions
  ranking/         configurable ranking (competition/dense/ordinal, ties)
  results/         central calculation engine, snapshots, publish, corrections
  reports/         report jobs (PDF/XLSX/CSV/HTML) over finalized results
  analytics/       exam dashboards, subject/school statistics, comparisons
  sms/             provider abstraction, templates, campaigns, async queue
  imports/         validated CSV/XLSX candidate & marks imports
  exports/         data exports (CSV/XLSX)
  audit/           append-only audit trail
  settings/        system settings, admin appconfig
  core/            base models, responses, exceptions, health, seed
```

### Examination lifecycle

```
DRAFT → READY → ACTIVE → MARKS_ENTRY → UNDER_REVIEW → FINALIZED → PUBLISHED → ARCHIVED
```

Transitions are enforced server-side by `ExaminationService`; `READY`
requires enrolled candidates + active subjects; `PUBLISHED` requires a
finalized exam and writes an immutable result snapshot.

### Multi-school examinations

`Examination.participating_schools` (M2M) allows one or many schools to take
part in a single examination. Candidate lists belong to schools; enrollment
validates that each list's school participates. Results are computed once
over the whole pool; school-level positions and statistics are derived from
the same engine.

### Marks, results, ranking

- Marks carry an explicit status (`ENTERED`, `ABSENT`, `EXEMPT`, `MISSING`,
  `PENDING`, `INVALID`); a missing mark is never silently treated as zero.
- Every mark change writes a `MarkChangeLog` (old/new value, actor, reason).
- `ResultCalculationService` is the single engine used by the API, reports,
  exports and snapshots: component totals → subject percentage → grade →
  candidate average → overall/list/school positions (configurable tie rule).
- Finalizing freezes a `ResultSnapshot` containing the grading scheme and
  ranking configuration used at that point in time.
- Corrections on published exams go through
  `ResultCorrectionRequest` (PENDING → APPROVED → APPLIED) with full audit.

### SMS notifications

- Short, configurable templates with whitelisted placeholders
  (`{candidate_name}`, `{position}`, `{exam_name}`, `{school_name}`,
  `{average}`, `{grade}`, `{division}`, `{total}`).
- Pluggable `SMSProvider` interface; built-ins: `console` (dev) and `webhook`
  (generic HTTP gateway). Custom providers subclass `BaseSMSProvider`.
- Campaigns queue messages asynchronously via Celery, dedupe candidates who
  already received SMS, skip invalid phones, log every delivery, and support
  explicit failed-resend.

## Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # edit values

createdb emas                  # or use docker-compose postgres
python manage.py migrate
python manage.py seed_demo     # demo schools/lists/exam/marks/results
python manage.py runserver
celery -A config worker -l info
```

Default demo login: `admin@demo.emas.local` / `admin12345`.

## API

Base path: `/api/v1/`. Interactive docs at `/api/docs/` (Swagger) and
`/api/redoc/`; raw schema at `/api/schema/`.

| Group | Endpoint |
| --- | --- |
| Auth | `auth/login/` `auth/register/` `auth/token/refresh/` `auth/logout/` `auth/logout-all/` `auth/me/` `auth/profile/` `auth/change-password/` `auth/forgot-password/` `auth/reset-password/` `auth/verify-email/` `auth/resend-verification/` `auth/accept-invitation/` `auth/sessions/` `auth/sessions/{id}/revoke/` `auth/audit-log/` |
| Users & roles | `users/` + `activate/` `suspend/` `deactivate/` `roles/`; `roles/`; `memberships/` |
| Schools | `schools/` |
| Candidates | `candidates/` |
| Candidate lists | `candidate-lists/` + `/import/` `/download/` `/add/` `/remove/` `/duplicate/` |
| Subjects | `subjects/` |
| Grading | `grading/schemes/` `/bands/` `/divisions/` |
| Examinations | `examinations/` + `/{id}/transition/` `/{id}/structure/` `/{id}/candidates/` `/{id}/dashboard/` |
| Exam subjects | `exam-subjects/` + `/{id}/lock-marks/` `/{id}/unlock-marks/` |
| Enrollments | `enrollments/` + `enroll_lists/` `preview/` |
| Marks | `marks/entries/` + `sheet/` `entry/` `bulk-entry/` `completion/` `change-log/` |
| Results | `results/workflow/` `results/examination/` `results/subjects/` `results/snapshots/` `results/corrections/` |
| Rankings | `rankings/` `rankings/examination/` `rankings/subject/` `rankings/schools/` |
| Reports | `reports/` + `generate/` `preview/` `download/` `templates/` |
| Analytics | `analytics/dashboard/` `analytics/examination/` `analytics/compare/` |
| SMS | `sms/templates/` `sms/campaigns/` + `send/` `preview/` `resend-failed/` `sms/messages/` |
| Audit | `audit/` |
| Imports | `imports/` + `upload/` `template/` |
| Exports | `exports/` + `download/` |
| Health | `/health/` `/health/live/` `/health/ready/` |

All endpoints return `{success, data|error, message}` envelopes. JWT via
`Authorization: Bearer <token>`; multi-tenant school context via
`X-School-Id` header (users are members of schools with roles).

### Authentication & authorization

- JWT login with rotating + blacklisted refresh tokens (SimpleJWT).
- `UserSession` per login (device, IP, UA, refresh-JTI identifier) — list,
  revoke, and logout-all supported; revocation blacklists the refresh token.
- Account statuses: `ACTIVE` / `INACTIVE` / `SUSPENDED` / `PENDING_VERIFICATION`
  (`is_active` stays in sync automatically).
- Secure, hashed, single-use, expiring tokens for password reset, email
  verification and admin invitations — raw secrets are never stored.
- Data-driven permissions: `Permission` + `RolePermission` + `UserRole`
  models; effective permissions are exposed on `/auth/me/` and enforceable
  via the `HasPermission` / `permission_required(...)` DRF classes.
- Brute-force protection: configurable failed-attempt lockout via the cache
  backend (`LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`).
- Configurable password policy (`PASSWORD_*` env vars) plus a common-password
  denylist; validated server-side.
- Account-enumeration protection: forgot-password and resend-verification
  always return generic responses; login errors are always "Invalid email or
  password."
- Every security event is appended to `AuthenticationAuditLog` (login
  success/failure/blocked, resets, verification, invitations, status and
  role changes, session revocation).

### Roles

| Role | Capabilities |
| --- | --- |
| `SUPER_ADMIN` | Everything, across all schools |
| `EXAM_ADMIN` | Create/manage examinations, candidates, lists, subjects, components, marks, results, reports, SMS |
| `MARKS_ENTRY` | View assigned examinations/subjects, enter and update draft marks |
| `REPORT_VIEWER` | View results, generate/export reports |
| `SCHOOL_COORDINATOR` | School-scoped candidate/list management |

## Testing

```bash
DJANGO_SETTINGS_MODULE=config.settings.test python manage.py test apps.core.tests
# or
pytest
```

71 tests cover auth/isolation, lifecycle transitions, enrollment dedupe,
multi-school exams, mark validation/statuses, component calculation methods,
grading/division, ranking ties (competition/dense), publish/snapshot flow,
the correction workflow, SMS queueing/dedupe/resend, and imports/exports.

## Deployment

- `Dockerfile` + `docker-compose.yml`: app (Gunicorn), Celery worker,
  PostgreSQL 16, Redis 7.
- For a bare VPS: run Gunicorn behind Nginx (reverse proxy + static/media),
  PostgreSQL and Redis locally, Celery worker via systemd.
- Configuration is fully environment-driven; see `.env.example`.
- Health checks: `/health/live/` (liveness), `/health/ready/` (DB + Redis).

## Design notes

- All business logic lives in service modules — views stay thin.
- Bulk operations (enrollment, marks, results, SMS, imports, reports) are
  transactional and run through Celery for large payloads.
- Important operations are idempotent: re-enrolling a list, re-calculating
  results or re-sending a campaign does not duplicate records.
- Every security-relevant action writes an `AuditLog` entry.



cd /opt/jointexams && git pull && docker compose -f deploy/docker-compose.prod.yml up -d --build
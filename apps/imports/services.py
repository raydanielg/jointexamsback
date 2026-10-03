import logging

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.candidates.models import Candidate
from apps.candidates.validators import normalize_phone, validate_phone_number
from apps.core.exceptions import BusinessRuleError

from .models import ImportSession
from .parsers import parse_spreadsheet, write_error_report

logger = logging.getLogger("emas.imports")


class ImportService:
    """Validate-then-commit CSV/XLSX imports inside a transaction.

    ``strict`` sessions abort on any row error; non-strict sessions commit
    valid rows and record failures with a downloadable error report.
    """

    GENDERS = {"male": "MALE", "m": "MALE", "female": "FEMALE", "f": "FEMALE"}

    @staticmethod
    def process(session: ImportSession):
        try:
            with transaction.atomic():
                session = ImportSession.objects.select_for_update().get(pk=session.pk)
                session.status = ImportSession.Status.PROCESSING
                session.save(update_fields=["status", "updated_at"])
                if session.import_type == ImportSession.ImportType.CANDIDATES:
                    result = ImportService._import_candidates(session)
                elif session.import_type == ImportSession.ImportType.MARKS:
                    result = ImportService._import_marks(session)
                else:
                    raise BusinessRuleError("Unsupported import type.", code="VALIDATION_ERROR")

                session.total_rows = result["total"]
                session.success_rows = result["success"]
                session.failed_rows = result["failed"]
                session.duplicate_rows = result["duplicates"]
                session.updated_rows = result.get("updated", 0)
                session.skipped_rows = result.get("skipped", 0)
                session.errors = result["errors"][:500]
                if result["errors"]:
                    session.error_report.save(
                        f"errors_{session.pk}.xlsx",
                        ContentFile(write_error_report(result["errors"])),
                        save=False,
                    )
                session.status = ImportSession.Status.COMPLETED
                session.completed_at = timezone.now()
                session.save()
            log_action(
                actor=session.created_by, action="IMPORT_COMPLETED", entity=session,
                school=session.school,
                metadata={
                    "type": session.import_type,
                    "success": result["success"],
                    "failed": result["failed"],
                },
            )
            return session
        except Exception as exc:
            session.status = ImportSession.Status.FAILED
            details = getattr(exc, "details", None) or {}
            session.errors = details.get("errors") or [{"row": 0, "message": str(exc)}]
            session.save(update_fields=["status", "errors", "updated_at"])
            logger.exception("Import session %s failed", session.pk)
            raise

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------
    @staticmethod
    def _import_candidates(session):
        """Columns: candidate_number, first_name, middle_name, last_name,
        gender, phone, guardian_name, guardian_phone."""
        rows = parse_spreadsheet(session.file, session.file.name)
        params = session.params or {}
        school = session.school
        # Optional center: candidates belong to a center (child school) of the
        # importing organization instead of the org itself.
        center = None
        if params.get("center"):
            from apps.schools.models import School

            from django.db.models import Q as _Q

            center = School.objects.filter(
                _Q(pk=params["center"])
                & (_Q(parent=school) | _Q(pk=school.pk))
            ).first()
            if center is None:
                raise BusinessRuleError(
                    "School not found under this organization.", code="NOT_FOUND"
                )
        errors = []

        target_school = center or school
        # Edited preview rows may be posted back instead of the raw file.
        rows = params.get("edited_rows") or rows

        parsed = ImportService._candidate_rows(rows, target_school)
        if parsed["errors"] and session.strict:
            raise BusinessRuleError(
                f"Import aborted: {len(parsed['errors'])} validation error(s).",
                code="IMPORT_VALIDATION_FAILED",
                details={"errors": parsed["errors"][:200]},
            )
        valid_rows = parsed["valid"]
        duplicates = parsed["duplicates"]
        errors = parsed["errors"]

        if errors and session.strict:
            raise BusinessRuleError(
                f"Import aborted: {len(errors)} validation error(s).",
                code="IMPORT_VALIDATION_FAILED",
                details={"errors": errors[:200]},
            )

        candidates = [
            Candidate(
                school=target_school,
                candidate_number=number,
                first_name=row.get("first_name", "").strip(),
                middle_name=(row.get("middle_name") or "").strip(),
                last_name=row.get("last_name", "").strip(),
                gender=gender,
                phone=normalize_phone(row.get("phone") or ""),
                guardian_name=(row.get("guardian_name") or row.get("parent_name") or "").strip(),
                guardian_phone=normalize_phone(
                    row.get("guardian_phone") or row.get("parent_phone") or ""
                ),
            )
            for _, row, number, gender in valid_rows
        ]
        Candidate.objects.bulk_create(candidates)

        # Optionally attach imported candidates to a candidate list.
        target_list = params.get("candidate_list")
        if target_list:
            from apps.candidate_lists.models import CandidateList, CandidateListEntry

            lst = CandidateList.objects.filter(pk=target_list, school=school).first()
            if lst:
                CandidateListEntry.objects.bulk_create(
                    [CandidateListEntry(list=lst, candidate=c, added_by=session.created_by) for c in candidates],
                    ignore_conflicts=True,
                )

        return {
            "total": len(rows),
            "success": len(candidates),
            "failed": len(rows) - len(candidates),
            "duplicates": duplicates,
            "errors": errors,
        }

    @staticmethod
    def _candidate_rows(rows, target_school):
        """Validate/normalise candidate rows. Returns per-row preview plus the
        valid payloads ready for bulk_create."""
        existing = set(
            Candidate.objects.filter(school=target_school).values_list("candidate_number", flat=True)
        )
        seen_in_file = set()
        valid_rows = []
        errors = []
        preview = []
        duplicates = 0
        auto_seq = 0

        def next_number():
            nonlocal auto_seq
            while True:
                auto_seq += 1
                candidate_no = f"{target_school.school_code}-{auto_seq:04d}"
                if candidate_no not in existing and candidate_no not in seen_in_file:
                    return candidate_no

        for index, row in enumerate(rows, start=2):  # header is row 1
            row = dict(row)
            row_errors = []
            full_name = (row.get("full_name") or row.get("name") or "").strip()
            if full_name:
                parts = full_name.split()
                row["first_name"] = parts[0]
                row["last_name"] = parts[-1] if len(parts) > 1 else parts[0]
                row["middle_name"] = " ".join(parts[1:-1])
            number = (row.get("candidate_number") or row.get("admission_number") or "").strip()
            auto_number = not number
            if auto_number:
                number = next_number()
            elif number in existing or number in seen_in_file:
                row_errors.append({"row": index, "field": "candidate_number", "message": "Duplicate candidate number."})
                duplicates += 1
            if not (row.get("first_name") or "").strip():
                row_errors.append({"row": index, "field": "full_name", "message": "Required."})
            if not (row.get("last_name") or "").strip():
                row_errors.append({"row": index, "field": "full_name", "message": "Required."})
            gender_raw = (row.get("gender") or "").strip().lower()
            gender = ImportService.GENDERS.get(gender_raw, Candidate.Gender.UNSPECIFIED)
            if gender_raw and gender_raw not in ImportService.GENDERS:
                row_errors.append({"row": index, "field": "gender", "message": "Invalid gender."})
            phone = (row.get("phone") or "").strip()
            if phone:
                try:
                    row["phone"] = normalize_phone(phone)
                    validate_phone_number(row["phone"])
                except Exception:
                    row_errors.append({"row": index, "field": "phone", "message": "Invalid phone number."})
            for field in ("guardian_phone", "guardian_phone_2", "parent_phone"):
                value = (row.get(field) or "").strip()
                if value:
                    try:
                        validate_phone_number(normalize_phone(value))
                    except Exception:
                        row_errors.append({"row": index, "field": field, "message": "Invalid phone number."})

            name = full_name or " ".join(
                filter(None, [row.get("first_name", ""), row.get("middle_name", ""), row.get("last_name", "")])
            ).strip()
            preview.append({
                "row": index,
                "candidate_number": number,
                "full_name": name,
                "phone": row.get("phone", ""),
                "status": "error" if row_errors else "ok",
                "messages": [e["message"] if e["field"] != "full_name" else f'{e["field"]}: {e["message"]}' for e in row_errors],
            })
            if row_errors:
                errors.extend(row_errors)
            else:
                seen_in_file.add(number)
                valid_rows.append((index, row, number, gender))
        return {"valid": valid_rows, "errors": errors, "preview": preview, "duplicates": duplicates}

    @staticmethod
    def preview_candidates(rows, target_school):
        """Dry-run validation for the upload preview step."""
        return ImportService._candidate_rows(rows, target_school)["preview"]

    # ------------------------------------------------------------------
    # Marks
    # ------------------------------------------------------------------
    @staticmethod
    def _import_marks(session):
        """Columns: candidate_number, subject_code, component_code
        (optional), marks, status (optional)."""
        from apps.enrollment.models import ExaminationCandidate
        from apps.examinations.models import Examination
        from apps.marks.services import MarksService

        rows = parse_spreadsheet(session.file, session.file.name)
        params = session.params or {}
        exam = Examination.objects.filter(pk=params.get("examination")).first()
        if exam is None:
            raise BusinessRuleError("Examination not found for marks import.", code="NOT_FOUND")

        exam_subjects = {
            es.subject.code.lower(): es
            for es in exam.exam_subjects.filter(status="ACTIVE").select_related("subject")
        }
        enrollments = {
            e.candidate_number: e
            for e in exam.candidates.filter(
                status__in=ExaminationCandidate.PARTICIPATING
            ).select_related("candidate")
        }
        component_index = {}
        for es in exam_subjects.values():
            for c in es.components.all():
                component_index[(es.pk, c.code.lower())] = c

        errors = []
        staged = []
        updated = 0
        for index, row in enumerate(rows, start=2):
            number = (row.get("candidate_number") or "").strip()
            enrollment = enrollments.get(number)
            subject_key = (row.get("subject") or row.get("subject_code") or "").strip().lower()
            exam_subject = exam_subjects.get(subject_key)
            component_code = (row.get("component") or row.get("component_code") or "").strip().lower()
            raw_value = (row.get("marks") or row.get("value") or "").strip()
            status_raw = (row.get("status") or "").strip().upper()

            if enrollment is None:
                errors.append({"row": index, "field": "candidate_number", "message": "Candidate not enrolled."})
                continue
            if exam_subject is None:
                errors.append({"row": index, "field": "subject", "message": f"Unknown subject '{subject_key}'."})
                continue
            if component_code:
                component = component_index.get((exam_subject.pk, component_code))
            else:
                components = list(exam_subject.components.filter(status="ACTIVE"))
                component = components[0] if len(components) == 1 else None
            if component is None:
                errors.append({"row": index, "field": "component", "message": "Unknown or ambiguous component."})
                continue
            staged.append((enrollment, component, raw_value or None, status_raw or None))

        if errors and session.strict:
            raise BusinessRuleError(
                f"Import aborted: {len(errors)} validation error(s).",
                code="IMPORT_VALIDATION_FAILED",
                details={"errors": errors[:200]},
            )

        from apps.marks.models import Mark

        existing_marks = {
            (m.exam_candidate_id, m.component_id)
            for m in Mark.objects.filter(exam_candidate__examination=exam)
        }
        success = 0
        for enrollment, component, raw_value, status_raw in staged:
            key = (enrollment.pk, component.pk)
            try:
                MarksService.set_mark(
                    enrollment,
                    component,
                    value=raw_value,
                    status=status_raw or None,
                    actor=session.created_by,
                    reason=f"Import {session.pk}",
                )
                if key in existing_marks:
                    updated += 1
                success += 1
            except BusinessRuleError as exc:
                errors.append({"row": 0, "field": "marks", "message": exc.message})

        if errors and session.strict:
            raise BusinessRuleError(
                "Import aborted during commit validation.", code="IMPORT_VALIDATION_FAILED",
                details={"errors": errors[:200]},
            )
        return {
            "total": len(rows),
            "success": success,
            "failed": len(rows) - success,
            "duplicates": 0,
            "updated": updated,
            "errors": errors,
        }

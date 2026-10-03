from django.db import transaction

from apps.audit.services import log_action
from apps.candidates.models import Candidate

from .models import CandidateList, CandidateListEntry


class CandidateListService:
    @staticmethod
    @transaction.atomic
    def add_candidates(clist: CandidateList, candidate_ids, actor=None, request=None):
        existing = set(
            CandidateListEntry.objects.filter(list=clist, candidate_id__in=candidate_ids)
            .values_list("candidate_id", flat=True)
        )
        if request is not None:
            from apps.accounts.permissions import accessible_school_ids

            candidates = Candidate.objects.filter(
                pk__in=candidate_ids, school_id__in=accessible_school_ids(request)
            )
        else:
            candidates = Candidate.objects.filter(
                pk__in=candidate_ids, school_id=clist.school_id
            )
        new_entries = [
            CandidateListEntry(list=clist, candidate=c, added_by=actor)
            for c in candidates
            if c.pk not in existing
        ]
        CandidateListEntry.objects.bulk_create(new_entries, ignore_conflicts=True)
        if new_entries:
            log_action(
                actor=actor, action="LIST_CANDIDATES_ADD", entity=clist, school=clist.school,
                metadata={"count": len(new_entries)},
            )
        return len(new_entries)

    @staticmethod
    @transaction.atomic
    def remove_candidates(clist: CandidateList, candidate_ids, actor=None):
        deleted, _ = CandidateListEntry.objects.filter(
            list=clist, candidate_id__in=candidate_ids
        ).delete()
        if deleted:
            log_action(
                actor=actor, action="LIST_CANDIDATES_REMOVE", entity=clist, school=clist.school,
                metadata={"count": deleted},
            )
        return deleted

    @staticmethod
    @transaction.atomic
    def duplicate(clist: CandidateList, new_name: str, actor=None):
        if CandidateList.objects.filter(school=clist.school, name=new_name).exists():
            from apps.core.exceptions import BusinessRuleError

            raise BusinessRuleError("A list with this name already exists.", code="DUPLICATE")
        copy = CandidateList.objects.create(
            school=clist.school,
            name=new_name,
            cohort=clist.cohort,
            description=clist.description,
            created_by=actor,
        )
        entries = [
            CandidateListEntry(list=copy, candidate_id=e.candidate_id, added_by=actor)
            for e in clist.entries.all()
        ]
        CandidateListEntry.objects.bulk_create(entries)
        log_action(actor=actor, action="LIST_DUPLICATED", entity=copy, school=copy.school)
        return copy

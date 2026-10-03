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
            # Unnumbered new members continue the list's current sequence
            # (PREFIX/YEAR/NNNN) so numbering stays contiguous until the user
            # explicitly regenerates.
            unnumbered = [e.candidate for e in new_entries if not e.candidate.candidate_number]
            if unnumbered:
                stats = {}
                for num in clist.candidates.exclude(
                    pk__in=[e.candidate_id for e in new_entries]
                ).values_list("candidate_number", flat=True):
                    prefix, _, suffix = (num or "").rpartition("/")
                    if prefix and suffix.isdigit():
                        count, hi = stats.get(prefix, (0, 0))
                        stats[prefix] = (count + 1, max(hi, int(suffix)))
                if stats:
                    prefix, (_, hi) = max(stats.items(), key=lambda kv: kv[1][0])
                    seq = hi
                    taken = set(
                        Candidate.objects.filter(
                            school_id__in=[c.school_id for c in unnumbered],
                            candidate_number__startswith=f"{prefix}/",
                        ).values_list("school_id", "candidate_number")
                    )
                    for c in unnumbered:
                        seq += 1
                        while (c.school_id, f"{prefix}/{seq:04d}") in taken:
                            seq += 1
                        c.candidate_number = f"{prefix}/{seq:04d}"
                        taken.add((c.school_id, c.candidate_number))
                    Candidate.objects.bulk_update(unnumbered, ["candidate_number"])
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

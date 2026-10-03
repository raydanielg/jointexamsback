import django_filters
from django.db.models import Q

from .models import Candidate


class CandidateFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(method="filter_name")
    candidate_list = django_filters.UUIDFilter(
        field_name="list_memberships__list", distinct=True
    )

    class Meta:
        model = Candidate
        fields = ("gender", "status", "candidate_number", "school")

    def filter_name(self, qs, name, value):
        return qs.filter(
            Q(first_name__icontains=value)
            | Q(middle_name__icontains=value)
            | Q(last_name__icontains=value)
        )

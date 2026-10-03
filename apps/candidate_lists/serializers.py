from apps.accounts.permissions import write_school
from rest_framework import serializers

from apps.candidates.models import Candidate
from apps.candidates.serializers import CandidateSerializer

from .models import CandidateList, CandidateListEntry


class CandidateListSerializer(serializers.ModelSerializer):
    candidate_count = serializers.IntegerField(read_only=True)
    school_name = serializers.CharField(source="school.school_name", read_only=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, default=None)

    class Meta:
        model = CandidateList
        fields = (
            "id",
            "name",
            "cohort",
            "description",
            "status",
            "candidate_count",
            "school",
            "school_name",
            "created_by_email",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "school", "created_at", "updated_at")

    def create(self, validated_data):
        validated_data["school"] = write_school(self.context["request"])
        validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)


class CandidateListEntrySerializer(serializers.ModelSerializer):
    candidate = CandidateSerializer(read_only=True)

    class Meta:
        model = CandidateListEntry
        fields = ("id", "candidate", "added_by", "created_at")


class CandidateListMembersSerializer(serializers.Serializer):
    candidate_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False
    )

    def validate_candidate_ids(self, value):
        from apps.accounts.permissions import accessible_school_ids

        found = Candidate.objects.filter(
            pk__in=value, school__in=accessible_school_ids(self.context["request"])
        ).count()
        if found != len(set(value)):
            raise serializers.ValidationError("Some candidates were not found in your organizations.")
        return value


class DuplicateListSerializer(serializers.Serializer):
    new_name = serializers.CharField(max_length=255)

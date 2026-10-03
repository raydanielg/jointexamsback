from rest_framework import serializers

from .models import ExaminationCandidate


class ExaminationCandidateSerializer(serializers.ModelSerializer):
    candidate_number = serializers.CharField(source="candidate.candidate_number", read_only=True)
    candidate_name = serializers.CharField(source="candidate.full_name", read_only=True)
    school_name = serializers.CharField(source="school.school_name", read_only=True)
    school_code = serializers.CharField(source="school.school_code", read_only=True)
    source_list_name = serializers.CharField(source="source_list.name", read_only=True, default=None)

    class Meta:
        model = ExaminationCandidate
        fields = (
            "id", "examination", "candidate", "candidate_number", "candidate_name",
            "school", "school_name", "school_code", "status", "source_list",
            "source_list_name", "enrolled_by", "created_at",
        )
        read_only_fields = fields


class EnrollListsSerializer(serializers.Serializer):
    list_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class EnrollCandidatesSerializer(serializers.Serializer):
    candidate_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class EnrollmentPreviewSerializer(serializers.Serializer):
    list_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    candidate_ids = serializers.ListField(child=serializers.UUIDField(), required=False)

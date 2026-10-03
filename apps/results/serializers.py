from rest_framework import serializers

from .models import (
    CandidateExamResult,
    CandidateSubjectResult,
    PredefinedComment,
    ResultCorrectionRequest,
    ResultSnapshot,
)


class CandidateSubjectResultSerializer(serializers.ModelSerializer):
    candidate_name = serializers.CharField(source="exam_candidate.candidate.full_name", read_only=True)
    candidate_number = serializers.CharField(source="exam_candidate.candidate.candidate_number", read_only=True)
    school_name = serializers.CharField(source="exam_candidate.school.school_name", read_only=True)
    subject_name = serializers.CharField(source="exam_subject.subject.name", read_only=True)
    subject_code = serializers.CharField(source="exam_subject.subject.code", read_only=True)

    class Meta:
        model = CandidateSubjectResult
        fields = (
            "id", "examination", "exam_candidate", "candidate_name", "candidate_number",
            "school_name", "exam_subject", "subject_name", "subject_code",
            "score", "max_score", "percentage", "grade", "points", "is_pass",
            "status", "position", "school_position", "component_breakdown",
            "calc_version", "computed_at",
        )
        read_only_fields = fields


class CandidateExamResultSerializer(serializers.ModelSerializer):
    candidate_name = serializers.CharField(source="exam_candidate.candidate.full_name", read_only=True)
    candidate_number = serializers.CharField(source="exam_candidate.candidate.candidate_number", read_only=True)
    school_name = serializers.CharField(source="exam_candidate.school.school_name", read_only=True)
    subjects = CandidateSubjectResultSerializer(
        source="exam_candidate.subject_results", many=True, read_only=True
    )

    class Meta:
        model = CandidateExamResult
        fields = (
            "id", "examination", "exam_candidate", "candidate_name", "candidate_number",
            "school_name", "total_score", "total_max", "average_percentage", "grade",
            "points_sum", "division", "position", "school_position", "list_position",
            "status", "subject_count", "scored_subjects", "subjects",
            "calc_version", "computed_at",
        )
        read_only_fields = fields


class ResultSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResultSnapshot
        fields = (
            "id", "examination", "kind", "version", "grading_scheme_snapshot",
            "ranking_config_snapshot", "created_by", "created_at",
        )
        read_only_fields = fields


class ResultSnapshotDetailSerializer(ResultSnapshotSerializer):
    class Meta(ResultSnapshotSerializer.Meta):
        fields = ResultSnapshotSerializer.Meta.fields + ("payload",)


class ResultCorrectionRequestSerializer(serializers.ModelSerializer):
    candidate_name = serializers.CharField(source="exam_candidate.candidate.full_name", read_only=True)
    candidate_number = serializers.CharField(source="exam_candidate.candidate.candidate_number", read_only=True)
    component_name = serializers.CharField(source="component.name", read_only=True)
    subject_name = serializers.CharField(source="component.exam_subject.subject.name", read_only=True)
    requested_by_email = serializers.EmailField(source="requested_by.email", read_only=True, default=None)
    reviewed_by_email = serializers.EmailField(source="reviewed_by.email", read_only=True, default=None)

    class Meta:
        model = ResultCorrectionRequest
        fields = (
            "id", "examination", "exam_candidate", "candidate_name", "candidate_number",
            "component", "component_name", "subject_name", "old_value", "old_status",
            "requested_value", "requested_status", "reason", "status",
            "requested_by_email", "reviewed_by_email", "review_note", "reviewed_at",
            "applied_at", "created_at",
        )
        read_only_fields = (
            "id", "old_value", "old_status", "status", "requested_by_email",
            "reviewed_by_email", "review_note", "reviewed_at", "applied_at", "created_at",
        )


class CorrectionCreateSerializer(serializers.Serializer):
    exam_candidate = serializers.UUIDField()
    component = serializers.UUIDField()
    requested_value = serializers.DecimalField(
        max_digits=8, decimal_places=2, required=False, allow_null=True
    )
    requested_status = serializers.CharField(required=False, allow_blank=True)
    reason = serializers.CharField()


class ReviewCorrectionSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    note = serializers.CharField(required=False, allow_blank=True)


class PredefinedCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PredefinedComment
        fields = ("id", "code", "text", "category", "created_at")
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        validated_data["school"] = self.context["request"].school
        return super().create(validated_data)

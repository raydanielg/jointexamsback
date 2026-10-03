from rest_framework import serializers

from .models import Mark, MarkChangeLog, MarkComment


class MarkSerializer(serializers.ModelSerializer):
    candidate_name = serializers.CharField(source="exam_candidate.candidate.full_name", read_only=True)
    candidate_number = serializers.CharField(source="exam_candidate.candidate_number", read_only=True)
    school_name = serializers.CharField(source="exam_candidate.school.school_name", read_only=True)
    component_name = serializers.CharField(source="component.name", read_only=True)
    component_code = serializers.CharField(source="component.code", read_only=True)
    subject_name = serializers.CharField(source="component.exam_subject.subject.name", read_only=True)
    entered_by_email = serializers.EmailField(source="entered_by.email", read_only=True, default=None)

    class Meta:
        model = Mark
        fields = (
            "id", "exam_candidate", "candidate_name", "candidate_number", "school_name",
            "component", "component_name", "component_code", "subject_name",
            "value", "status", "remarks", "entered_by_email", "created_at", "updated_at",
        )
        read_only_fields = fields


class MarkChangeLogSerializer(serializers.ModelSerializer):
    changed_by_email = serializers.EmailField(source="changed_by.email", read_only=True, default=None)
    candidate_number = serializers.CharField(source="exam_candidate.candidate_number", read_only=True)
    component_code = serializers.CharField(source="component.code", read_only=True)
    subject_name = serializers.CharField(source="component.exam_subject.subject.name", read_only=True)

    class Meta:
        model = MarkChangeLog
        fields = (
            "id", "mark", "exam_candidate", "candidate_number", "component",
            "component_code", "subject_name", "action", "old_value", "new_value",
            "old_status", "new_status", "reason", "changed_by", "changed_by_email",
            "ip_address", "created_at",
        )
        read_only_fields = fields


class MarkCommentSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, default=None)

    class Meta:
        model = MarkComment
        fields = (
            "id", "examination", "exam_candidate", "exam_subject", "scope",
            "body", "created_by_email", "created_at",
        )
        read_only_fields = ("id", "created_at")


class MarkEntryRowSerializer(serializers.Serializer):
    exam_candidate = serializers.UUIDField(required=False)
    candidate_number = serializers.CharField(required=False, allow_blank=True)
    component = serializers.CharField(required=False, allow_blank=True)
    component_code = serializers.CharField(required=False, allow_blank=True)
    value = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, allow_null=True)
    status = serializers.ChoiceField(choices=Mark.Status.choices, required=False)
    reason = serializers.CharField(required=False, allow_blank=True)


class BulkMarksSerializer(serializers.Serializer):
    rows = MarkEntryRowSerializer(many=True)


class MarksSheetRowSerializer(serializers.Serializer):
    """One row of the marks-entry sheet for a single component."""

    exam_candidate_id = serializers.UUIDField()
    candidate_number = serializers.CharField()
    candidate_name = serializers.CharField()
    school_name = serializers.CharField()
    mark_id = serializers.UUIDField(allow_null=True)
    value = serializers.DecimalField(max_digits=8, decimal_places=2, allow_null=True)
    status = serializers.CharField()
    remarks = serializers.CharField(allow_blank=True)

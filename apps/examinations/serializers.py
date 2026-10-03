from apps.accounts.permissions import write_school
from django.db.models import Count
from rest_framework import serializers

from .models import ExamComponent, Examination, ExamSubject


class ExamComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamComponent
        fields = (
            "id", "exam_subject", "name", "code", "maximum_marks", "weight",
            "required", "display_order", "status",
        )
        read_only_fields = ("id", "exam_subject")

    def validate_weight(self, value):
        if value is not None and (value <= 0 or value > 100):
            raise serializers.ValidationError("Weight must be between 0 and 100.")
        return value


class ExamSubjectSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    subject_code = serializers.CharField(source="subject.code", read_only=True)
    components = ExamComponentSerializer(many=True, read_only=True)
    marks_completion = serializers.SerializerMethodField()

    class Meta:
        model = ExamSubject
        fields = (
            "id", "examination", "subject", "subject_name", "subject_code",
            "maximum_marks", "pass_mark", "weight", "calculation_method",
            "display_order", "marks_status", "status", "components", "marks_completion",
        )
        read_only_fields = ("id", "examination")

    def get_marks_completion(self, obj):
        from apps.marks.models import Mark

        components = list(obj.components.filter(status="ACTIVE"))
        if not components:
            return None
        expected = len(components) * obj.examination.candidates.filter(
            status__in=("ACTIVE", "COMPLETED")
        ).count()
        if expected == 0:
            return None
        entered = Mark.objects.filter(
            component__in=components,
            exam_candidate__examination=obj.examination,
        ).exclude(status=Mark.Status.PENDING).count()
        return round(entered / expected * 100, 1)


class ExaminationListSerializer(serializers.ModelSerializer):
    candidate_count = serializers.SerializerMethodField()
    subject_count = serializers.SerializerMethodField()
    school_count = serializers.SerializerMethodField()
    organizer_name = serializers.CharField(source="school.school_name", read_only=True)

    class Meta:
        model = Examination
        fields = (
            "id", "name", "code", "term", "period", "start_date", "end_date", "status",
            "public_token",
            "candidate_count", "subject_count", "school_count", "organizer_name",
            "published_at", "created_at",
        )

    def get_candidate_count(self, obj):
        return getattr(obj, "_candidate_count", None) or obj.candidates.filter(
            status__in=("ACTIVE", "COMPLETED")
        ).count()

    def get_subject_count(self, obj):
        return getattr(obj, "_subject_count", None) or obj.exam_subjects.filter(
            status="ACTIVE"
        ).count()

    def get_school_count(self, obj):
        return getattr(obj, "_school_count", None) or (
            obj.participating_schools.count() or 1
        )


class ExaminationSerializer(serializers.ModelSerializer):
    subjects_detail = ExamSubjectSerializer(source="exam_subjects", many=True, read_only=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, default=None)
    organizer_name = serializers.CharField(source="school.school_name", read_only=True)
    participating_school_names = serializers.SlugRelatedField(
        source="participating_schools", read_only=True, many=True, slug_field="school_name"
    )
    candidate_list_names = serializers.SlugRelatedField(
        source="candidate_lists", read_only=True, many=True, slug_field="name"
    )

    class Meta:
        model = Examination
        fields = (
            "id", "name", "code", "description", "term", "period",
            "start_date", "end_date", "instructions", "grading_scheme",
            "ranking_config", "report_template", "sms_template",
            "division_best_subjects", "status", "school", "organizer_name",
            "participating_schools", "participating_school_names",
            "candidate_lists", "candidate_list_names",
            "created_by", "created_by_email", "published_at", "finalized_at",
            "subjects_detail", "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "school", "status", "created_by", "published_at", "finalized_at",
            "created_at", "updated_at",
        )

    def validate_code(self, value):
        school = self.context["request"].school
        qs = Examination.objects.filter(school=school, code=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An examination with this code already exists.")
        return value

    def validate_participating_schools(self, schools):
        for s in schools:
            if s.status != "ACTIVE":
                raise serializers.ValidationError(
                    f"School '{s.school_name}' is inactive and cannot join examinations."
                )
        return schools

    def create(self, validated_data):
        request = self.context["request"]
        validated_data["school"] = write_school(request)
        validated_data["created_by"] = request.user
        participating = validated_data.pop("participating_schools", [])
        lists = validated_data.pop("candidate_lists", [])
        exam = Examination.objects.create(**validated_data)
        if participating:
            exam.participating_schools.set(participating)
        if lists:
            exam.candidate_lists.set(lists)
        return exam

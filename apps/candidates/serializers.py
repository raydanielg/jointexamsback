from apps.accounts.permissions import write_school
from rest_framework import serializers

from apps.core.validators import validate_image_file

from .models import Candidate
from .validators import validate_phone_number


class CandidateSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    school_name = serializers.CharField(source="school.school_name", read_only=True)
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Candidate
        fields = (
            "id",
            "candidate_number",
            "first_name",
            "middle_name",
            "last_name",
            "full_name",
            "gender",
            "phone",
            "guardian_name",
            "guardian_phone",
            "guardian_phone_2",
            "photo",
            "photo_url",
            "status",
            "school",
            "school_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "school", "school_name", "created_at", "updated_at")

    def get_photo_url(self, obj):
        if obj.photo:
            request = self.context.get("request")
            return request.build_absolute_uri(obj.photo.url) if request else obj.photo.url
        return None

    def validate(self, attrs):
        for field in ("phone", "guardian_phone", "guardian_phone_2"):
            value = attrs.get(field)
            if value:
                try:
                    attrs[field] = validate_phone_number(value)
                except Exception as exc:
                    raise serializers.ValidationError({field: "Invalid phone number."}) from exc
        return attrs

    def validate_photo(self, file):
        if file:
            validate_image_file(file)
        return file

    def validate_candidate_number(self, value):
        school = self.context["request"].school
        qs = Candidate.objects.filter(school=school, candidate_number=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A candidate with this number already exists.")
        return value

    def create(self, validated_data):
        validated_data["school"] = write_school(self.context["request"])
        return super().create(validated_data)

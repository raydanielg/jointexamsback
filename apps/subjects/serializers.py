from apps.accounts.permissions import write_school
from rest_framework import serializers

from .models import Subject


class SubjectSerializer(serializers.ModelSerializer):
    is_global = serializers.SerializerMethodField()

    class Meta:
        model = Subject
        fields = (
            "id",
            "name",
            "code",
            "short_name",
            "description",
            "status",
            "is_global",
            "created_at",
        )
        read_only_fields = ("id", "created_at")

    def get_is_global(self, obj):
        return obj.school_id is None

    def create(self, validated_data):
        request = self.context["request"]
        # Only super admins create global subjects.
        if request.user.is_superadmin and request.data.get("is_global"):
            validated_data["school"] = None
        else:
            validated_data["school"] = write_school(request)
        return super().create(validated_data)

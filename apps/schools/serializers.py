from rest_framework import serializers

from .models import School


class SchoolSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = School
        fields = (
            "id",
            "parent",
            "school_name",
            "school_code",
            "registration_number",
            "location",
            "phone",
            "email",
            "logo",
            "logo_url",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_logo_url(self, obj):
        if obj.logo:
            request = self.context.get("request")
            return request.build_absolute_uri(obj.logo.url) if request else obj.logo.url
        return None

    def validate_logo(self, file):
        if file:
            from apps.core.validators import validate_image_file

            validate_image_file(file)
        return file

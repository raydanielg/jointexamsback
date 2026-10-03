from rest_framework import serializers

from .models import SchoolSetting, SystemSetting


class SchoolSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolSetting
        fields = ("id", "key", "value", "description", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def create(self, validated_data):
        validated_data["school"] = self.context["request"].school
        return super().create(validated_data)


class SystemSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemSetting
        fields = ("id", "key", "value", "description", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

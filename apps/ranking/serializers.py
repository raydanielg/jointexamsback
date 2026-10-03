from rest_framework import serializers

from .models import RankingConfiguration


class RankingConfigurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = RankingConfiguration
        fields = (
            "id", "name", "method", "rank_by", "include_absent", "is_default",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def create(self, validated_data):
        validated_data["school"] = self.context["request"].school
        return super().create(validated_data)

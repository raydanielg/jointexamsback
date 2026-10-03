from rest_framework import serializers

from .models import DivisionBand, GradeBand, GradingScheme


class GradeBandSerializer(serializers.ModelSerializer):
    class Meta:
        model = GradeBand
        fields = (
            "id",
            "grade",
            "min_percentage",
            "max_percentage",
            "points",
            "is_pass",
            "remark",
            "display_order",
        )


class DivisionBandSerializer(serializers.ModelSerializer):
    class Meta:
        model = DivisionBand
        fields = ("id", "name", "min_points", "max_points", "display_order")


class GradingSchemeSerializer(serializers.ModelSerializer):
    bands = GradeBandSerializer(many=True, read_only=True)
    division_bands = DivisionBandSerializer(many=True, read_only=True)

    class Meta:
        model = GradingScheme
        fields = (
            "id",
            "name",
            "description",
            "is_default",
            "status",
            "bands",
            "division_bands",
            "created_at",
        )
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        validated_data["school"] = self.context["request"].school
        scheme = super().create(validated_data)
        if scheme.is_default:
            GradingScheme.objects.filter(school=scheme.school, is_default=True).exclude(
                pk=scheme.pk
            ).update(is_default=False)
        return scheme

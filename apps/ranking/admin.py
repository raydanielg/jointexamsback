from django.contrib import admin

from .models import RankingConfiguration


@admin.register(RankingConfiguration)
class RankingConfigurationAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "method", "rank_by", "is_default")
    list_filter = ("method", "is_default")

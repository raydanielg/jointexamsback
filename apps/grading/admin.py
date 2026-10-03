from django.contrib import admin

from .models import DivisionBand, GradeBand, GradingScheme


class GradeBandInline(admin.TabularInline):
    model = GradeBand
    extra = 0


class DivisionBandInline(admin.TabularInline):
    model = DivisionBand
    extra = 0


@admin.register(GradingScheme)
class GradingSchemeAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "is_default", "status")
    list_filter = ("school", "is_default", "status")
    inlines = [GradeBandInline, DivisionBandInline]

from django.contrib import admin

from .models import SchoolSetting, SystemSetting


@admin.register(SchoolSetting)
class SchoolSettingAdmin(admin.ModelAdmin):
    list_display = ("school", "key", "value")
    list_filter = ("school",)
    search_fields = ("key",)


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value")
    search_fields = ("key",)

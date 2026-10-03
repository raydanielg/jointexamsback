from django.core.cache import cache

from .models import SchoolSetting


def get_school_setting(school, key, default=None):
    cache_key = f"emas:school_setting:{school.pk}:{key}"
    value = cache.get(cache_key)
    if value is None:
        setting = SchoolSetting.objects.filter(school=school, key=key).first()
        value = setting.value if setting else default
        cache.set(cache_key, value, 300)
    return value


def set_school_setting(school, key, value, description=""):
    setting, _ = SchoolSetting.objects.update_or_create(
        school=school, key=key, defaults={"value": value, "description": description}
    )
    cache.delete(f"emas:school_setting:{school.pk}:{key}")
    return setting

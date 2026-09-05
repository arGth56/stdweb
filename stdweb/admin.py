from django.contrib import admin
from django import forms

from .models import Task, Preset, UserProfile, LightcurvePoint, AlertMeta, TelegramNotice, TelegramSubscriber

from .forms import PrettyJSONEncoder

class TaskAdmin(admin.ModelAdmin):
    search_fields = ['original_name', 'title', 'user__username']
    list_display = ['id', 'user', 'state', 'original_name', 'title']
    list_display_links = list_display

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['config'].encoder = PrettyJSONEncoder

        return form


admin.site.register(Task, TaskAdmin)


class PresetAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'config']
    list_display_links = list_display

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['config'].encoder = PrettyJSONEncoder

        return form


admin.site.register(Preset, PresetAdmin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'affiliation', 'share_public_lightcurves']
    list_filter = ['share_public_lightcurves']
    search_fields = ['user__username', 'user__email', 'affiliation']


@admin.register(AlertMeta)
class AlertMetaAdmin(admin.ModelAdmin):
    list_display = ['name_key', 'tns_name', 'obj_type', 'discovery_iso', 'source', 'fetched']
    search_fields = ['name_key', 'display_name', 'tns_name', 'host_name']


@admin.register(LightcurvePoint)
class LightcurvePointAdmin(admin.ModelAdmin):
    list_display = ['id', 'task', 'user', 'filt', 'mjd', 'mag', 'is_detection', 'published']
    list_filter = ['filt', 'is_detection', 'is_diff', 'published']
    search_fields = ['target_name', 'user__username']


@admin.register(TelegramNotice)
class TelegramNoticeAdmin(admin.ModelAdmin):
    list_display = ['id', 'object_name', 'user', 'created']
    search_fields = ['object_name', 'title', 'user__username']


@admin.register(TelegramSubscriber)
class TelegramSubscriberAdmin(admin.ModelAdmin):
    list_display = ['email', 'confirmed', 'created']
    list_filter = ['confirmed']
    search_fields = ['email']

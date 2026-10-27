from django.contrib import admin

from .models import Click


@admin.register(Click)
class ClickAdmin(admin.ModelAdmin):
    list_display = ("link", "clicked_at", "browser", "os", "device_type", "country_name", "referrer_domain", "is_bot")
    list_filter = ("is_bot", "device_type", "browser", "country_name")
    list_select_related = ("link",)
    date_hierarchy = "clicked_at"
    raw_id_fields = ("link",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

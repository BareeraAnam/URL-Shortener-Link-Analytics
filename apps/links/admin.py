from django.contrib import admin

from .models import BulkImportJob, Link


@admin.register(Link)
class LinkAdmin(admin.ModelAdmin):
    list_display = ("slug", "original_url", "owner", "click_count", "is_active", "expires_at", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("slug", "original_url", "title", "owner__username")
    readonly_fields = ("click_count", "last_clicked_at", "created_at", "updated_at")
    raw_id_fields = ("owner",)
    actions = ["disable_links", "enable_links"]

    @admin.action(description="Disable selected links (takes effect immediately)")
    def disable_links(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} link(s) disabled.")

    @admin.action(description="Enable selected links")
    def enable_links(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} link(s) enabled.")


@admin.register(BulkImportJob)
class BulkImportJobAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "status", "total_rows", "success_count", "failed_count", "created_at")
    list_filter = ("status",)
    readonly_fields = ("errors", "payload", "created_at", "updated_at", "finished_at")

from django import forms
from django.contrib import admin
from django.contrib.auth.hashers import identify_hasher

from .models import BulkImportJob, Link


class LinkAdminForm(forms.ModelForm):
    new_password = forms.CharField(
        label="New password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Leave empty to keep the current password. It is stored as a hash.",
    )
    clear_password = forms.BooleanField(
        label="Remove password (make the link public)",
        required=False,
    )

    class Meta:
        model = Link
        fields = "__all__"


@admin.register(Link)
class LinkAdmin(admin.ModelAdmin):
    form = LinkAdminForm
    list_display = ("slug", "original_url", "owner", "click_count", "is_active", "expires_at", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("slug", "original_url", "title", "owner__username")
    readonly_fields = ("password_status", "click_count", "last_clicked_at", "created_at", "updated_at")
    raw_id_fields = ("owner",)
    exclude = ("password",)
    actions = ["disable_links", "enable_links"]

    @admin.display(description="Password")
    def password_status(self, obj):
        if obj is not None and obj.password:
            return "Protected (a hash is stored)"
        return "None (public link)"

    def save_model(self, request, obj, form, change):
        """The raw hash is never shown. A new password is hashed before saving."""
        data = getattr(form, "cleaned_data", {})
        if data.get("clear_password"):
            obj.password = ""
        elif data.get("new_password"):
            obj.set_password(data["new_password"])
        elif obj.password:
            try:
                identify_hasher(obj.password)  # already a valid hash -> keep as is
            except ValueError:
                obj.set_password(obj.password)  # plain text -> hash it
        super().save_model(request, obj, form, change)

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

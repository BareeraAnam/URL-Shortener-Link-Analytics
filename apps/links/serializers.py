from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from .models import BulkImportJob, Link
from .services import generate_slug, slug_taken


class LinkSerializer(serializers.ModelSerializer):
    short_url = serializers.ReadOnlyField()
    has_password = serializers.ReadOnlyField()
    qr_code_url = serializers.SerializerMethodField()
    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=128,
        trim_whitespace=False,
        help_text="Set to protect the link. Send null/'' on update to remove protection.",
    )

    class Meta:
        model = Link
        fields = [
            "id",
            "slug",
            "short_url",
            "original_url",
            "title",
            "password",
            "has_password",
            "expires_at",
            "is_active",
            "click_count",
            "last_clicked_at",
            "qr_code_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "click_count", "last_clicked_at", "created_at", "updated_at"]
        extra_kwargs = {"slug": {"required": False}}

    def get_qr_code_url(self, obj) -> str:
        path = reverse("link-qr", args=[obj.pk])
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    def validate_slug(self, value):
        if slug_taken(value, exclude_pk=self.instance.pk if self.instance else None):
            raise serializers.ValidationError("This slug is already taken (slugs are case-insensitive).")
        return value

    def validate_expires_at(self, value):
        if value and self.instance is None and value <= timezone.now():
            raise serializers.ValidationError("Expiry must be in the future.")
        return value

    def validate(self, attrs):
        slug = attrs.get("slug")
        if self.instance and slug and slug != self.instance.slug:
            raise serializers.ValidationError({"slug": "Slug cannot be changed after creation."})
        return attrs

    def create(self, validated_data):
        raw_password = validated_data.pop("password", None)
        custom_slug = "slug" in validated_data
        owner = validated_data.get("owner")
        if owner is not None and Link.objects.filter(owner=owner).count() >= settings.MAX_LINKS_PER_USER:
            raise serializers.ValidationError(f"Link limit reached ({settings.MAX_LINKS_PER_USER} links per account).")
        hash_cache = self.context.get("password_hashes")  # shared by all rows of one bulk import
        for _ in range(5):
            link = Link(**validated_data)
            if not custom_slug:
                link.slug = generate_slug()
            link.set_password(raw_password, hash_cache)
            try:
                with transaction.atomic():
                    link.save()
                return link
            except IntegrityError:
                if custom_slug:
                    raise serializers.ValidationError({"slug": "This slug is already taken."}) from None
        raise serializers.ValidationError("Could not allocate a unique slug, please retry.")

    def update(self, instance, validated_data):
        if "password" in validated_data:
            instance.set_password(validated_data.pop("password"))
        validated_data.pop("slug", None)
        return super().update(instance, validated_data)


class BulkImportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulkImportJob
        fields = [
            "id",
            "file_name",
            "status",
            "total_rows",
            "success_count",
            "failed_count",
            "errors",
            "created_at",
            "finished_at",
        ]
        read_only_fields = fields


class QRQuerySerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["png", "svg"], default="png")
    size = serializers.IntegerField(min_value=1, max_value=20, default=10, help_text="Pixels per module")
    border = serializers.IntegerField(min_value=0, max_value=10, default=4)
    download = serializers.BooleanField(default=False)


class SlugCheckSerializer(serializers.Serializer):
    slug = serializers.CharField()

from rest_framework import serializers

from .models import Click


class AnalyticsQuerySerializer(serializers.Serializer):
    days = serializers.IntegerField(min_value=1, max_value=365, default=30)
    include_bots = serializers.BooleanField(default=False)


class ClickSerializer(serializers.ModelSerializer):
    class Meta:
        model = Click
        fields = [
            "id",
            "clicked_at",
            "browser",
            "browser_version",
            "os",
            "os_version",
            "device_type",
            "is_bot",
            "country_code",
            "country_name",
            "referrer_domain",
            "referrer",
        ]
        read_only_fields = fields

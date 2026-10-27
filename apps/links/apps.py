from django.apps import AppConfig


class LinksConfig(AppConfig):
    name = "apps.links"

    def ready(self):
        from . import signals  # noqa: F401

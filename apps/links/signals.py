from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .cache import invalidate
from .models import Link


def _invalidate(slug: str) -> None:
    # Delete now AND after commit: the second delete closes the race where a
    # concurrent redirect re-cached the old row before our transaction committed.
    invalidate(slug)
    transaction.on_commit(lambda: invalidate(slug))


@receiver(post_save, sender=Link)
def link_saved(sender, instance, **kwargs):
    _invalidate(instance.slug)


@receiver(post_delete, sender=Link)
def link_deleted(sender, instance, **kwargs):
    _invalidate(instance.slug)

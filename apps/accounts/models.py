from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user from day one (swapping AUTH_USER_MODEL later is painful)."""

    email = models.EmailField("email address", unique=True)

    REQUIRED_FIELDS = ["email"]

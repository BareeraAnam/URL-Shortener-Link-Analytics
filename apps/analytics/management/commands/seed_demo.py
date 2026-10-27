import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.analytics.models import Click
from apps.analytics.tracking import visitor_hash
from apps.links.models import Link

BROWSERS = [
    ("Chrome", "Windows", "desktop"),
    ("Safari", "iOS", "mobile"),
    ("Firefox", "Linux", "desktop"),
    ("Edge", "Windows", "desktop"),
    ("Chrome Mobile", "Android", "mobile"),
]
COUNTRIES = [("US", "United States"), ("PK", "Pakistan"), ("IN", "India"), ("GB", "United Kingdom"), ("DE", "Germany")]
REFERRERS = ["direct", "twitter.com", "linkedin.com", "google.com", "facebook.com", "news.ycombinator.com"]


class Command(BaseCommand):
    help = "Create a demo user (demo / demo12345!) with sample links and ~600 fake clicks."

    def handle(self, *args, **options):
        user, _ = get_user_model().objects.get_or_create(username="demo", defaults={"email": "demo@example.com"})
        user.set_password("demo12345!")
        user.save()
        now = timezone.now()
        demo = [
            ("launch", "https://example.com/product-launch", "Product launch"),
            ("guide", "https://example.com/docs/getting-started", "Guide"),
            ("promo", "https://example.com/black-friday", "Black Friday promo"),
        ]
        for slug, url, title in demo:
            link, _ = Link.objects.get_or_create(
                slug=slug, defaults={"owner": user, "original_url": url, "title": title}
            )
            clicks = []
            for _i in range(200):
                when = now - timedelta(days=random.randint(0, 29), minutes=random.randint(0, 1439))
                browser, os_name, device = random.choice(BROWSERS)
                code, country = random.choice(COUNTRIES)
                ref = random.choice(REFERRERS)
                clicks.append(
                    Click(
                        link=link,
                        clicked_at=when,
                        browser=browser,
                        os=os_name,
                        device_type=device,
                        country_code=code,
                        country_name=country,
                        referrer_domain=ref,
                        ip_hash=visitor_hash(f"10.0.{random.randint(0, 40)}.{random.randint(1, 250)}", browser, when),
                    )
                )
            Click.objects.bulk_create(clicks)
            Link.objects.filter(pk=link.pk).update(click_count=link.click_count + len(clicks))
        self.stdout.write(self.style.SUCCESS("Demo ready. Login: demo / demo12345!"))

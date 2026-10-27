import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

URL = "https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-Country&license_key={key}&suffix=tar.gz"


class Command(BaseCommand):
    help = "Download GeoLite2-Country.mmdb (needs a free MaxMind licence key in MAXMIND_LICENSE_KEY)."

    def handle(self, *args, **options):
        key = os.environ.get("MAXMIND_LICENSE_KEY")
        if not key:
            raise CommandError("Set MAXMIND_LICENSE_KEY (free at https://www.maxmind.com/en/geolite2/signup).")
        dest = Path(settings.GEOIP_COUNTRY_DB)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".tar.gz") as tmp:
            with urllib.request.urlopen(URL.format(key=key), timeout=120) as response:  # noqa: S310
                shutil.copyfileobj(response, tmp)
            tmp.flush()
            with tarfile.open(tmp.name) as tar:
                member = next((m for m in tar.getmembers() if m.name.endswith(".mmdb")), None)
                if member is None:
                    raise CommandError("No .mmdb file found in the downloaded archive.")
                with tar.extractfile(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        self.stdout.write(self.style.SUCCESS(f"GeoIP database saved to {dest}"))

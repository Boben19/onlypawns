import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from pawn.models import BackupLog


class Command(BaseCommand):
    help = "Zip the database, uploaded photos and encrypted ID images into backups/. Run it daily."

    def handle(self, *args, **opts):
        settings.BACKUP_DIR.mkdir(exist_ok=True)
        target = settings.BACKUP_DIR / f"onlypawns-{timezone.localtime():%Y%m%d-%H%M%S}.zip"
        try:
            with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
                db = settings.DATABASES["default"]
                if db["ENGINE"].endswith("sqlite3"):
                    copy = Path(tmp) / "db.sqlite3"
                    src, dst = sqlite3.connect(db["NAME"]), sqlite3.connect(copy)
                    src.backup(dst)
                    src.close()
                    dst.close()
                    z.write(copy, "db.sqlite3")
                else:
                    from django.core.management import call_command
                    dump = Path(tmp) / "dump.json"
                    with open(dump, "w") as fh:
                        call_command("dumpdata", "--natural-foreign", stdout=fh)
                    z.write(dump, "dump.json")
                for folder in (settings.MEDIA_ROOT, settings.PRIVATE_ROOT):
                    if Path(folder).exists():
                        for f in Path(folder).rglob("*"):
                            if f.is_file():
                                z.write(f, f"{Path(folder).name}/{f.relative_to(folder)}")
            size = round(target.stat().st_size / 1048576, 2)
            BackupLog.objects.create(file_path=str(target), size_mb=size, status="Success")
            for old in sorted(settings.BACKUP_DIR.glob("onlypawns-*.zip"))[:-14]:
                old.unlink()
            self.stdout.write(f"Backup saved: {target} ({size} MB)")
        except Exception as e:
            BackupLog.objects.create(file_path=str(target), status="Failed")
            self.stderr.write(f"Backup failed: {e}")

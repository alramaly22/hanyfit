"""Shrink + convert the review screenshots in accounts/static/images/reviews/.

    python manage.py optimize_reviews

For every PNG/JPG/JPEG in the folder: downscale to at most 1600px wide (the
aspect ratio is kept), save as WebP (quality 82) next to it, and delete the
original. Files already in WebP are only downscaled if they are too wide.
Re-running is safe. Then add the resulting file names to accounts/reviews.py.
"""

from pathlib import Path

from django.core.management.base import BaseCommand
from PIL import Image, ImageOps

MAX_WIDTH = 1600
QUALITY = 82
FOLDER = Path(__file__).resolve().parents[2] / "static" / "images" / "reviews"


class Command(BaseCommand):
    help = "Convert/shrink review screenshots to web-friendly WebP."

    def handle(self, *args, **options):
        files = sorted(
            p for p in FOLDER.iterdir()
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
        if not files:
            self.stdout.write(f"No images found in {FOLDER}")
            return

        for src in files:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                too_wide = im.width > MAX_WIDTH
                if src.suffix.lower() == ".webp" and not too_wide:
                    self.stdout.write(f"kept    {src.name} ({im.width}x{im.height})")
                    continue
                if too_wide:
                    im = im.resize(
                        (MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)),
                        Image.LANCZOS,
                    )
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGB")
                dest = src.with_suffix(".webp")
                before = src.stat().st_size
                im.save(dest, "WEBP", quality=QUALITY, method=6)
            if dest != src:
                src.unlink()
            self.stdout.write(
                f"wrote   {dest.name}  {before // 1024}KB -> {dest.stat().st_size // 1024}KB"
            )

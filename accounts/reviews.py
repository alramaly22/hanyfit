"""Customer Reviews section -- the one place the review screenshots are managed.

HOW TO ADD A SCREENSHOT
-----------------------
Just put the image in  accounts/static/images/reviews/  (WebP/JPG/PNG; run
`python manage.py optimize_reviews` to shrink + convert them to WebP). Every
image in that folder is shown automatically, sorted by file name
(review-01, review-02, ...).

Optional: list file names in REVIEW_IMAGES below to choose the exact order or
set custom alt text -- when the list is not empty, only those are shown.

That is all. Width/height are read from the file so the page never jumps
while images load and nothing is ever stretched. A name that is listed but
whose file is missing is skipped (never a broken image), and if the list is
empty the whole section is simply not rendered.

Only real screenshots belong here -- nothing is generated.
"""

import re
from functools import lru_cache
from pathlib import Path

REVIEWS_STATIC_DIR = "images/reviews"

# Either a plain file name, or a dict when you want a custom description for
# screen readers:  {"file": "review-01.webp", "alt": "Review from a client"}
REVIEW_IMAGES = [
    "review-1.jpeg",
    "review-2.jpeg",
    "review-3.jpeg",
    "review-4.jpeg",
]

_FOLDER = Path(__file__).resolve().parent / "static" / REVIEWS_STATIC_DIR
_EXTS = {".webp", ".jpg", ".jpeg", ".png"}
_DEFAULT_ALT = "Customer review screenshot / صورة رأي عميل"


@lru_cache(maxsize=256)
def _size(file_name):
    """(width, height) of an image, or (None, None) if it can't be read."""
    try:
        from PIL import Image

        with Image.open(_FOLDER / file_name) as img:
            return img.size
    except Exception:
        return (None, None)


def get_reviews():
    """The reviews to render, as dicts: path, alt, width, height."""
    items = []
    entries = REVIEW_IMAGES or sorted(
        (p.name for p in _FOLDER.glob("*") if p.suffix.lower() in _EXTS),
        key=lambda n: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", n)],
    )
    for entry in entries:
        if isinstance(entry, str):
            entry = {"file": entry}
        name = (entry.get("file") or "").strip()
        # File names only -- never let a path component through.
        if not name or Path(name).name != name or not (_FOLDER / name).is_file():
            continue
        width, height = _size(name)
        items.append(
            {
                "path": f"{REVIEWS_STATIC_DIR}/{name}",
                "alt": entry.get("alt") or _DEFAULT_ALT,
                "width": width,
                "height": height,
            }
        )
    return items

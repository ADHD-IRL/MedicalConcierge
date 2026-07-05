"""Turns an uploaded file (PDF or image) into a list of page images that the
multimodal extractor can send to the vision model.

PDFs are always rasterized rather than text-extracted: many real-world medical
PDFs are scanned/faxed with no text layer, so going through the vision path
uniformly is simpler than a text-layer-first-with-image-fallback branch, and a
printed PDF page is trivially easy for a vision model to read anyway.

Everything is sized for the vision API before it leaves this module: the API
rejects individual images over ~5 MB and downscales anything beyond ~1568 px
on the long side internally, so sending a 12 MP phone photo raw is both a
"file too large" error waiting to happen and wasted upload. Oversized images
are downscaled to VISION_MAX_DIM_PX and re-encoded as JPEG.
"""

from __future__ import annotations

import fitz  # PyMuPDF

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# The vision API downscales to ~1568 px on the long side anyway; sending more
# pixels costs upload size/time and buys no reading accuracy.
VISION_MAX_DIM_PX = 1568
# Images at or under both limits pass through untouched.
PASSTHROUGH_MAX_BYTES = 1_500_000
JPEG_QUALITY = 80
# Guardrail for absurd inputs; ~50 prepared pages also stays comfortably
# under the API's total-request budget.
MAX_PDF_PAGES = 50

_FITZ_IMAGE_TYPE = {".jpg": "jpg", ".jpeg": "jpg", ".png": "png", ".webp": "webp"}


class UnsupportedFileType(ValueError):
    pass


def load_as_images(filename: str, data: bytes, dpi: int = 200) -> list[tuple[bytes, str]]:
    """Returns a list of (image_bytes, mime_type) tuples, one per page for PDFs,
    or a single entry for image files - every entry already sized for the
    vision API."""

    suffix = _suffix(filename)

    if suffix == ".pdf":
        return _rasterize_pdf(data, dpi=dpi)

    if suffix in SUPPORTED_IMAGE_SUFFIXES:
        return [_prepare_image(data, suffix)]

    raise UnsupportedFileType(
        f"Unsupported file type '{suffix}'. Supported: pdf, jpg, jpeg, png, webp."
    )


def _suffix(filename: str) -> str:
    lower = filename.lower()
    idx = lower.rfind(".")
    return lower[idx:] if idx != -1 else ""


def _prepare_image(data: bytes, suffix: str) -> tuple[bytes, str]:
    """Downscale/re-encode an image only when needed; small images pass
    through byte-for-byte."""

    mime = SUPPORTED_IMAGE_MIME[suffix]
    try:
        probe = fitz.Pixmap(data)
        long_px = max(probe.width, probe.height)
    except Exception:
        # Format fitz can't decode (some WebP variants). Pass small files
        # through for the vision API to try; reject oversized ones with a
        # message the user can act on.
        if len(data) <= PASSTHROUGH_MAX_BYTES:
            return data, mime
        raise UnsupportedFileType(
            "This image is too large to send for reading and couldn't be "
            "shrunk automatically. Please re-save it as JPG or PNG and try again."
        )

    if long_px <= VISION_MAX_DIM_PX and len(data) <= PASSTHROUGH_MAX_BYTES:
        return data, mime

    with fitz.open(stream=data, filetype=_FITZ_IMAGE_TYPE[suffix]) as doc:
        page = doc[0]
        # page.rect is in points; scaling relative to it lands exactly on the
        # requested pixel width regardless of the image's embedded DPI.
        rect_long = max(page.rect.width, page.rect.height)
        target_long = min(long_px, VISION_MAX_DIM_PX)
        zoom = target_long / rect_long
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY), "image/jpeg"


def _rasterize_pdf(data: bytes, dpi: int) -> list[tuple[bytes, str]]:
    images: list[tuple[bytes, str]] = []

    with fitz.open(stream=data, filetype="pdf") as doc:
        if len(doc) > MAX_PDF_PAGES:
            raise UnsupportedFileType(
                f"This PDF has {len(doc)} pages; the limit is {MAX_PDF_PAGES}. "
                "Please split it and upload the relevant part."
            )
        for page in doc:
            rect_long = max(page.rect.width, page.rect.height)
            # Requested DPI, but never beyond what the vision API will keep.
            zoom = min(dpi / 72.0, VISION_MAX_DIM_PX / rect_long)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            images.append((pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY), "image/jpeg"))

    if not images:
        raise UnsupportedFileType("PDF contained no pages.")

    return images

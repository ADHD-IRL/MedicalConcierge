"""Turns an uploaded file (PDF or image) into a list of page images (PNG bytes)
that the multimodal extractor can send to the vision model.

PDFs are always rasterized rather than text-extracted: many real-world medical
PDFs are scanned/faxed with no text layer, so going through the vision path
uniformly is simpler than a text-layer-first-with-image-fallback branch, and a
printed PDF page is trivially easy for a vision model to read anyway.
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


class UnsupportedFileType(ValueError):
    pass


def load_as_images(filename: str, data: bytes, dpi: int = 200) -> list[tuple[bytes, str]]:
    """Returns a list of (image_bytes, mime_type) tuples, one per page for PDFs,
    or a single entry for image files."""

    suffix = _suffix(filename)

    if suffix == ".pdf":
        return _rasterize_pdf(data, dpi=dpi)

    if suffix in SUPPORTED_IMAGE_SUFFIXES:
        return [(data, SUPPORTED_IMAGE_MIME[suffix])]

    raise UnsupportedFileType(
        f"Unsupported file type '{suffix}'. Supported: pdf, jpg, jpeg, png, webp."
    )


def _suffix(filename: str) -> str:
    lower = filename.lower()
    idx = lower.rfind(".")
    return lower[idx:] if idx != -1 else ""


def _rasterize_pdf(data: bytes, dpi: int) -> list[tuple[bytes, str]]:
    images: list[tuple[bytes, str]] = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            png_bytes = pix.tobytes("png")
            images.append((png_bytes, "image/png"))

    if not images:
        raise UnsupportedFileType("PDF contained no pages.")

    return images

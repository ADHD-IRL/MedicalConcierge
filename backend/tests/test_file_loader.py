import fitz
import pytest

from app.ingestion.file_loader import (
    MAX_PDF_PAGES,
    PASSTHROUGH_MAX_BYTES,
    VISION_MAX_DIM_PX,
    UnsupportedFileType,
    load_as_images,
)


def _solid_image_bytes(width: int, height: int, fmt: str = "png") -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height))
    pix.set_rect(pix.irect, (200, 180, 160))
    if fmt == "png":
        return pix.tobytes("png")
    return pix.tobytes("jpeg", jpg_quality=90)


def _dims(image_bytes: bytes) -> tuple[int, int]:
    pix = fitz.Pixmap(image_bytes)
    return pix.width, pix.height


def test_huge_photo_is_downscaled_and_jpeg_encoded():
    big = _solid_image_bytes(4000, 3000, "jpg")

    [(out, mime)] = load_as_images("phone_photo.jpg", big)

    w, h = _dims(out)
    assert max(w, h) == VISION_MAX_DIM_PX
    assert (w, h) == (VISION_MAX_DIM_PX, 1176)  # aspect preserved
    assert mime == "image/jpeg"


def test_small_image_passes_through_untouched():
    small = _solid_image_bytes(800, 600, "jpg")
    assert len(small) <= PASSTHROUGH_MAX_BYTES

    [(out, mime)] = load_as_images("bottle.jpg", small)

    assert out == small
    assert mime == "image/jpeg"


def test_small_dimensions_but_huge_bytes_still_reencoded():
    # Dimension check alone isn't enough - a bloated file with small
    # dimensions must still be shrunk. Random noise makes PNG incompressible.
    import os

    w, h = 1500, 1000
    pix = fitz.Pixmap(fitz.csRGB, w, h, os.urandom(w * h * 3), False)
    big_png = pix.tobytes("png")
    assert len(big_png) > PASSTHROUGH_MAX_BYTES

    [(out, mime)] = load_as_images("noisy.png", big_png)

    assert mime == "image/jpeg"
    assert len(out) < len(big_png)
    ow, oh = _dims(out)
    assert max(ow, oh) <= VISION_MAX_DIM_PX


def test_pdf_pages_capped_to_vision_dimensions():
    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page()  # letter: 612x792 pt
        page.insert_text((72, 72), "Rx: Lisinopril 10mg daily")
    pdf = doc.tobytes()
    doc.close()

    pages = load_as_images("note.pdf", pdf, dpi=300)

    assert len(pages) == 3
    for image_bytes, mime in pages:
        w, h = _dims(image_bytes)
        assert max(w, h) <= VISION_MAX_DIM_PX
        assert mime == "image/jpeg"


def test_pdf_page_count_limit():
    doc = fitz.open()
    for _ in range(MAX_PDF_PAGES + 1):
        doc.new_page()
    pdf = doc.tobytes()
    doc.close()

    with pytest.raises(UnsupportedFileType, match="limit is"):
        load_as_images("huge.pdf", pdf)

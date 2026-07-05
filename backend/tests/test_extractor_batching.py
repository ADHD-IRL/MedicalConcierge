from app.ingestion.multimodal_extractor import (
    BATCH_MAX_BYTES,
    BATCH_MAX_IMAGES,
    _batch_images,
)


def _img(size: int) -> tuple[bytes, str]:
    return (b"x" * size, "image/jpeg")


def test_small_document_stays_in_one_batch():
    images = [_img(200_000) for _ in range(5)]

    batches = _batch_images(images)

    assert len(batches) == 1
    assert batches[0] == images


def test_batches_split_by_cumulative_bytes():
    # 5 images of ~6MB: 15MB budget fits two per batch
    images = [_img(6_000_000) for _ in range(5)]

    batches = _batch_images(images)

    assert [len(b) for b in batches] == [2, 2, 1]
    for batch in batches:
        assert sum(len(i[0]) for i in batch) <= BATCH_MAX_BYTES


def test_batches_split_by_image_count():
    images = [_img(1_000) for _ in range(BATCH_MAX_IMAGES * 2 + 3)]

    batches = _batch_images(images)

    assert [len(b) for b in batches] == [BATCH_MAX_IMAGES, BATCH_MAX_IMAGES, 3]


def test_single_oversized_image_still_gets_a_batch():
    # prep should prevent this, but the batcher must not drop or loop on it
    images = [_img(BATCH_MAX_BYTES + 1)]

    batches = _batch_images(images)

    assert len(batches) == 1
    assert len(batches[0]) == 1


def test_order_is_preserved_across_batches():
    images = [(bytes([i]) * 6_000_000, "image/jpeg") for i in range(4)]

    batches = _batch_images(images)
    flattened = [img for batch in batches for img in batch]

    assert flattened == images

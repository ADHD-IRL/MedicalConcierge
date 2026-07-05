import pytest

import app.ingestion.multimodal_extractor as me
from app.schemas import RecordKind, SourceType


def _item(name: str) -> dict:
    return {
        "raw_text": f"{name} 5mg once daily",
        "name_as_written": name,
        "source_type": "printed_document",
        "extraction_confidence": 0.9,
        "ambiguities": [],
    }


class FakeBlock:
    type = "tool_use"
    name = me._TOOL_NAME

    def __init__(self, items):
        self.input = {"items": items}


class FakeResponse:
    def __init__(self, items, stop_reason="tool_use"):
        self.stop_reason = stop_reason
        self.content = [FakeBlock(items)]


class FakeClient:
    """Returns scripted responses in order; records each request's image count."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []  # list of (n_images, max_tokens)

        outer = self

        class _Messages:
            def create(self, **kwargs):
                n_images = sum(
                    1 for c in kwargs["messages"][0]["content"] if c["type"] == "image"
                )
                outer.calls.append((n_images, kwargs["max_tokens"]))
                return outer._script.pop(0)

        self.messages = _Messages()


def _run(monkeypatch, fake, images):
    monkeypatch.setattr(me.anthropic, "Anthropic", lambda api_key: fake)
    return me.extract_records(images, RecordKind.medicine, SourceType.printed_document)


PAGES = [(b"page-one", "image/jpeg"), (b"page-two", "image/jpeg")]


def test_truncated_batch_is_split_and_retried(monkeypatch):
    fake = FakeClient([
        FakeResponse([], stop_reason="max_tokens"),      # both pages: truncated
        FakeResponse([_item("Eliquis")]),                # page 1 alone
        FakeResponse([_item("Clopidogrel")]),            # page 2 alone
    ])

    items = _run(monkeypatch, fake, PAGES)

    assert [i.name_as_written for i in items] == ["Eliquis", "Clopidogrel"]
    assert [n for n, _ in fake.calls] == [2, 1, 1]


def test_single_page_truncation_raises_instead_of_returning_empty(monkeypatch):
    fake = FakeClient([FakeResponse([], stop_reason="max_tokens")])

    with pytest.raises(me.ExtractionTruncated):
        _run(monkeypatch, fake, [PAGES[0]])


def test_output_token_budget_raised(monkeypatch):
    fake = FakeClient([FakeResponse([_item("Metformin")])])

    _run(monkeypatch, fake, [PAGES[0]])

    assert fake.calls[0][1] == me.MAX_OUTPUT_TOKENS
    assert me.MAX_OUTPUT_TOKENS >= 16384


def test_malformed_items_are_dropped_not_fatal(monkeypatch):
    bad = {"name_as_written": "Mystery"}  # missing required fields
    fake = FakeClient([FakeResponse([_item("Eliquis"), bad])])

    items = _run(monkeypatch, fake, [PAGES[0]])

    assert [i.name_as_written for i in items] == ["Eliquis"]


def test_dense_document_batches_capped_at_five_pages(monkeypatch):
    pages = [(f"p{i}".encode(), "image/jpeg") for i in range(7)]
    fake = FakeClient([
        FakeResponse([_item("A")]),
        FakeResponse([_item("B")]),
    ])

    items = _run(monkeypatch, fake, pages)

    assert [n for n, _ in fake.calls] == [5, 2]
    assert len(items) == 2
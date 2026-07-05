from fastapi.testclient import TestClient

from app.api import routes
from app.main import app

client = TestClient(app)


def test_oversized_upload_rejected_with_friendly_413():
    blob = b"x" * (routes.MAX_UPLOAD_BYTES + 1)

    resp = client.post(
        "/api/ingest/medicine",
        files={"file": ("huge.jpg", blob, "image/jpeg")},
    )

    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert "limit is 30 MB" in detail


def test_unsupported_type_still_400():
    resp = client.post(
        "/api/ingest/medicine",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]

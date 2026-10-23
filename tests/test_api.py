"""
API Integration Test Suite.
Tests endpoints, validation, error handling, status codes, and PDF uploads.
"""
import io
import pytest
from fastapi.testclient import TestClient
from api.app import app

client = TestClient(app)


def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["ocr_available"] is True


def test_schema_endpoint():
    response = client.get("/api/schema")
    assert response.status_code == 200
    data = response.json()
    assert "properties" in data
    assert "supplier" in data["properties"]
    assert "customer" in data["properties"]
    assert "financials" in data["properties"]


def test_extract_valid_pdf():
    with open("6934.pdf", "rb") as f:
        response = client.post(
            "/api/invoices/extract",
            files={"file": ("6934.pdf", f, "application/pdf")}
        )
    assert response.status_code == 200
    data = response.json()
    assert data["extraction_method"] == "embedded_text"
    inv = data["data"]
    assert inv["invoice"]["invoice_number"] == "GJ/TR/006934"
    assert inv["financials"]["grand_total"] == 214514.0


def test_extract_empty_file():
    response = client.post(
        "/api/invoices/extract",
        files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")}
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_extract_invalid_extension():
    response = client.post(
        "/api/invoices/extract",
        files={"file": ("document.txt", io.BytesIO(b"Hello text"), "text/plain")}
    )
    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()


def test_extract_corrupt_pdf():
    response = client.post(
        "/api/invoices/extract",
        files={"file": ("corrupt.pdf", io.BytesIO(b"%PDF-1.4 garbage corrupt bytes"), "application/pdf")}
    )
    assert response.status_code == 422
    assert "corrupt" in response.json()["detail"].lower()

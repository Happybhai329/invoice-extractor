"""
FastAPI REST API for Format-Independent Invoice Data Extraction.
Provides endpoints for invoice extraction, schema discovery, health monitoring,
and serves the interactive web interface.
"""
import os
import io
import logging
from typing import Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import pymupdf

from invoice_extractor.engine import InvoiceExtractionEngine
from invoice_extractor.schema import ExtractionResponse, StandardInvoice

logger = logging.getLogger("invoice_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(
    title="Format-Independent Invoice Extraction API",
    description="Extracts normalized, validated structured data from invoices across diverse layouts, formats, and templates.",
    version="1.0.0"
)

# Engine initialization
engine = InvoiceExtractionEngine()

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


@app.get("/api/health", summary="Health check and engine readiness")
def health_check():
    """Returns engine readiness status and available document processors."""
    return {
        "status": "healthy",
        "service": "invoice-extraction-engine",
        "ocr_available": True,
        "supported_formats": ["application/pdf", ".pdf"],
        "max_file_size_mb": MAX_FILE_SIZE_BYTES / (1024 * 1024)
    }


@app.get("/api/schema", summary="Export Standard Invoice Schema")
def get_schema():
    """Returns the JSON schema definition for the StandardInvoice model."""
    return StandardInvoice.model_json_schema()


@app.post(
    "/api/invoices/extract",
    response_model=ExtractionResponse,
    summary="Extract structured data from an uploaded invoice PDF",
    status_code=status.HTTP_200_OK
)
async def extract_invoice(file: UploadFile = File(...)):
    """
    Accepts a PDF invoice file and returns normalized, validated structured JSON.
    Validates file format, detects empty or corrupted files, and executes the extraction pipeline.
    """
    # 1. Validate file extension
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{filename}'. Only PDF documents (.pdf) are supported."
        )

    # 2. Read bytes and validate file size
    try:
        contents = await file.read()
    except Exception as e:
        logger.error(f"Error reading uploaded file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to read the uploaded file payload."
        )

    if not contents or len(contents) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes). Please upload a valid invoice PDF."
        )

    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES / (1024 * 1024):.1f} MB."
        )

    # 3. Validate PDF structure with PyMuPDF
    try:
        doc = pymupdf.open(stream=contents, filetype="pdf")
        if doc.is_encrypted:
            doc.close()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The uploaded PDF is password-protected or encrypted. Please remove the password and retry."
            )
        if len(doc) == 0:
            doc.close()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The uploaded PDF contains zero pages."
            )
        doc.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Corrupt PDF upload attempt: {filename}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Uploaded file is corrupt or not a valid PDF document: {str(e)}"
        )

    # 4. Execute extraction pipeline
    try:
        logger.info(f"Processing invoice extraction for: {filename} ({len(contents)} bytes)")
        response = engine.extract(contents)
        return response
    except Exception as e:
        logger.error(f"Extraction pipeline failure on {filename}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during invoice extraction and layout analysis."
        )


# Static assets & Web Interface
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/demo-file/{filename:path}", summary="Serve demo invoice PDF safely")
def get_demo_file(filename: str):
    """Safely serves a demo invoice PDF matching case-insensitively."""
    base_name = os.path.basename(filename)
    if not base_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files can be loaded as demos")
    for f in os.listdir("."):
        if f.lower() == base_name.lower():
            from fastapi.responses import FileResponse
            return FileResponse(f, media_type="application/pdf")
    raise HTTPException(status_code=404, detail=f"Demo PDF '{filename}' not found in workspace")


@app.get("/{pdf_name:path}.pdf", summary="Serve sample demo invoice PDFs")
def serve_sample_pdf(pdf_name: str):
    """Allows demo UI to directly load sample PDFs."""
    base_name = os.path.basename(pdf_name) + ".pdf"
    for f in os.listdir("."):
        if f.lower() == base_name.lower():
            from fastapi.responses import FileResponse
            return FileResponse(f, media_type="application/pdf")
    raise HTTPException(status_code=404, detail="Sample PDF not found")


@app.get("/", response_class=HTMLResponse, summary="Serve Web User Interface")
def serve_ui():
    """Serves the interactive invoice extraction single-page interface."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return """
    <html>
        <head><title>Invoice Extraction API</title></head>
        <body style="font-family: sans-serif; padding: 40px;">
            <h2>Invoice Extraction API is Running</h2>
            <p>Access API docs at <a href="/docs">/docs</a> or upload via <code>POST /api/invoices/extract</code>.</p>
        </body>
    </html>
    """

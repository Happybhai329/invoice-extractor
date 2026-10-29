"""
Core Extraction Engine Coordinator.
Orchestrates PDF ingestion, layout analysis, OCR fallback, parsing,
normalization, validation, and structured response generation.
"""
from typing import Union, Optional
import os
import time
from invoice_extractor.schema import StandardInvoice, ExtractionResponse
from invoice_extractor.document_reader import DocumentReader, DocumentContent
from invoice_extractor.parser import InvoiceParser
from invoice_extractor.validator import InvoiceValidator
from invoice_extractor.ai_provider import ConfigurableAIProvider, BaseDocumentAIProvider


class InvoiceExtractionEngine:
    """End-to-end invoice extraction pipeline."""

    def __init__(
        self,
        reader: Optional[DocumentReader] = None,
        parser: Optional[InvoiceParser] = None,
        validator: Optional[InvoiceValidator] = None,
        ai_provider: Optional[BaseDocumentAIProvider] = None,
    ):
        self.reader = reader or DocumentReader()
        self.parser = parser or InvoiceParser()
        self.validator = validator or InvoiceValidator()
        self.ai_provider = ai_provider or ConfigurableAIProvider()

    def extract(self, source: Union[str, bytes], force_ai: bool = False) -> ExtractionResponse:
        """
        Executes the extraction pipeline:
        PDF Ingestion -> Digital/OCR extraction -> Parsing -> Normalization -> Validation.
        Seamlessly escalates unseen/degraded invoice layouts to Intelligent AI extraction.
        """
        start_time = time.time()

        # 1. Read document & layout (with automatic OCR fallback)
        doc_content: DocumentContent = self.reader.read_pdf(source)

        method = "ocr" if doc_content.is_scanned else "embedded_text"

        # 2. Extract structured data: Fast local deterministic parse first
        invoice: Optional[StandardInvoice] = None
        should_use_ai = force_ai or os.getenv("FORCE_AI", "").lower() in ("true", "1", "yes")

        if not should_use_ai:
            local_invoice = self.parser.parse(doc_content)
            warnings, confidence, needs_review = self.validator.validate(local_invoice)

            # Check if local extraction successfully extracted mandatory fields with high confidence
            is_high_quality = (
                local_invoice.invoice.invoice_number is not None
                and local_invoice.financials.grand_total is not None
                and len(local_invoice.line_items) > 0
                and confidence >= 0.80
                and not needs_review
            )

            if is_high_quality:
                invoice = local_invoice
            else:
                # Unseen format, missing critical fields, or low confidence -> escalate to AI Provider
                if self.ai_provider and self.ai_provider.is_available():
                    try:
                        ai_invoice = self.ai_provider.extract_invoice(source, doc_content.full_text)
                        if ai_invoice:
                            invoice = ai_invoice
                            method = "ai_assisted"
                        else:
                            invoice = local_invoice
                    except Exception:
                        invoice = local_invoice
                else:
                    invoice = local_invoice
        else:
            # Force AI path
            if self.ai_provider and self.ai_provider.is_available():
                try:
                    invoice = self.ai_provider.extract_invoice(source, doc_content.full_text)
                    if invoice:
                        method = "ai_assisted"
                except Exception:
                    invoice = None

            if not invoice:
                invoice = self.parser.parse(doc_content)

        if not invoice:
            invoice = self.parser.parse(doc_content)

        # 3. Validate and calibrate confidence
        warnings, confidence, needs_review = self.validator.validate(invoice)

        if doc_content.is_scanned:
            warnings.append("Document was processed via OCR (scanned pages detected).")
            # If multi-page scanned, check for multi-invoice presence
            if doc_content.page_count > 1:
                warnings.append("Multi-page scanned document: Multiple invoices may be bundled in this PDF.")

        # 4. Compile observability diagnostics
        diagnostics = {
            **doc_content.diagnostics,
            "extraction_method": method,
            "processing_time_ms": round((time.time() - start_time) * 1000, 1),
            "line_items_extracted": len(invoice.line_items),
            "tax_components_extracted": len(invoice.financials.tax_components),
            "fields_detected": [
                k for k, v in {
                    "supplier_name": invoice.supplier.name,
                    "customer_name": invoice.customer.name,
                    "invoice_number": invoice.invoice.invoice_number,
                    "invoice_date": invoice.invoice.invoice_date,
                    "due_date": invoice.invoice.due_date,
                    "po_number": invoice.invoice.purchase_order_number,
                    "currency": invoice.invoice.currency,
                    "subtotal": invoice.financials.subtotal,
                    "tax_amount": invoice.financials.total_tax_amount,
                    "grand_total": invoice.financials.grand_total,
                }.items() if v is not None
            ],
            "fields_missing": [
                k for k, v in {
                    "supplier_name": invoice.supplier.name,
                    "customer_name": invoice.customer.name,
                    "invoice_number": invoice.invoice.invoice_number,
                    "invoice_date": invoice.invoice.invoice_date,
                    "due_date": invoice.invoice.due_date,
                    "po_number": invoice.invoice.purchase_order_number,
                    "currency": invoice.invoice.currency,
                    "subtotal": invoice.financials.subtotal,
                    "tax_amount": invoice.financials.total_tax_amount,
                    "grand_total": invoice.financials.grand_total,
                }.items() if v is None
            ]
        }

        return ExtractionResponse(
            data=invoice,
            raw_text=doc_content.full_text,
            extraction_method=method,
            page_count=doc_content.page_count,
            warnings=warnings,
            confidence_score=confidence,
            needs_review=needs_review,
            diagnostics=diagnostics
        )

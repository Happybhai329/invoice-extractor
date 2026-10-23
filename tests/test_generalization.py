"""
Generalization and Robustness Test Suite for Invoice Extraction Engine.
Tests unseen layouts, arithmetic verification, edge-case date formats,
currency handling, missing optional fields, and observability diagnostics.
"""
import os
import pytest
from invoice_extractor.engine import InvoiceExtractionEngine
from invoice_extractor.currency_normalizer import detect_currency, normalize_amount
from invoice_extractor.date_normalizer import normalize_date
from invoice_extractor.schema import StandardInvoice, SupplierDetails, CustomerDetails, InvoiceDetails, FinancialDetails, LineItem
from invoice_extractor.validator import InvoiceValidator


@pytest.fixture(scope="module")
def engine():
    return InvoiceExtractionEngine()


def test_unseen_invoice_sliced_invoices(engine):
    """
    Validates that the system generalizes to the previously unseen
    wordpress-pdf-invoice-plugin-sample.pdf (Australian/USD-style web design invoice).
    """
    pdf_path = "wordpress-pdf-invoice-plugin-sample.pdf"
    assert os.path.exists(pdf_path), f"File {pdf_path} not found"

    response = engine.extract(pdf_path)
    assert response is not None
    inv = response.data

    # Supplier
    assert inv.supplier.name == "DEMO - Sliced Invoices"
    assert "Suite 5A-1204" in inv.supplier.address
    assert inv.supplier.email == "admin@slicedinvoices.com"

    # Customer
    assert inv.customer.name == "Test Business"
    assert "Melbourne" in inv.customer.billing_address
    assert inv.customer.email == "test@test.com"

    # Invoice Details
    assert inv.invoice.invoice_number == "INV-3337"
    assert inv.invoice.invoice_date == "2016-01-25"
    assert inv.invoice.due_date == "2016-01-31"
    assert inv.invoice.purchase_order_number == "12345"

    # Financials & Line Items
    assert inv.financials.subtotal == pytest.approx(85.00, rel=1e-2)
    assert inv.financials.total_tax_amount == pytest.approx(8.50, rel=1e-2)
    assert inv.financials.grand_total == pytest.approx(93.50, rel=1e-2)
    assert len(inv.line_items) == 1
    assert inv.line_items[0].quantity == 1.0
    assert inv.line_items[0].unit_price == 85.0
    assert inv.line_items[0].total == 85.0

    # Confidence must be high (no 20% regression)
    assert response.confidence_score >= 0.85
    assert not response.needs_review


def test_currency_normalizer_no_false_inr():
    """
    Ensures 'rs' in common words ('hrs', 'first', 'description', 'orders')
    does not trigger false positive INR currency detection.
    """
    sample_text = "Standard Web Design service for 40 hrs at $85/hr. First installment."
    currency = detect_currency(sample_text)
    assert currency == "USD"
    assert currency != "INR"

    eur_text = "Consulting fees: 1,500.00 € paid on first of the month."
    assert detect_currency(eur_text) == "EUR"

    gbp_text = "Total amount payable: £2,450.00 inclusive of VAT."
    assert detect_currency(gbp_text) == "GBP"

    inr_text = "Total Amount: Rs. 1,50,000/- payable in INR."
    assert detect_currency(inr_text) == "INR"


def test_date_normalizer_edge_cases():
    """Tests named months, slash formats, and ambiguous DMY vs MDY."""
    # Named months
    d1, amb1, conf1 = normalize_date("January 25, 2016")
    assert d1 == "2016-01-25"
    assert not amb1

    d2, amb2, conf2 = normalize_date("06/Jul/2025")
    assert d2 == "2025-07-06"
    assert not amb2

    # Unambiguous day > 12
    d3, amb3, conf3 = normalize_date("25/01/2024")
    assert d3 == "2024-01-25"
    assert not amb3

    # Ambiguous date (both day and month <= 12)
    d4, amb4, conf4 = normalize_date("04/05/2024")
    assert d4 == "2024-05-04"
    assert amb4 is True  # Flags ambiguity for audit


def test_missing_optional_fields_validation():
    """
    Ensures documents missing optional fields (no PO number, no due date, no discount)
    still pass validation with high confidence and no false rejection.
    """
    validator = InvoiceValidator()
    inv = StandardInvoice(
        supplier=SupplierDetails(name="Acme Corp"),
        customer=CustomerDetails(name="Beta LLC"),
        invoice=InvoiceDetails(
            invoice_number="INV-9999",
            invoice_date="2025-01-01",
            currency="USD",
        ),
        financials=FinancialDetails(
            subtotal=100.0,
            grand_total=100.0,
        ),
        line_items=[
            LineItem(
                line_number=1,
                description="General Consulting",
                quantity=1.0,
                unit_price=100.0,
                total=100.0,
            )
        ]
    )

    warnings, confidence, needs_review = validator.validate(inv)
    assert confidence >= 0.85
    assert not needs_review
    assert not any("mandatory" in w.lower() for w in warnings)


def test_diagnostics_observability(engine):
    """
    Verifies that the extraction response contains structured diagnostic metadata
    for debugging unseen document failures.
    """
    response = engine.extract("wordpress-pdf-invoice-plugin-sample.pdf")
    assert response.diagnostics is not None
    diag = response.diagnostics

    assert "page_count" in diag
    assert diag["page_count"] == 1
    assert "extraction_method" in diag
    assert diag["extraction_method"] == "embedded_text"
    assert "chars_per_page" in diag
    assert len(diag["chars_per_page"]) == 1
    assert diag["chars_per_page"][0] > 0
    assert "fields_detected" in diag
    assert "supplier_name" in diag["fields_detected"]
    assert "customer_name" in diag["fields_detected"]
    assert "invoice_number" in diag["fields_detected"]
    assert "grand_total" in diag["fields_detected"]
    assert "processing_time_ms" in diag

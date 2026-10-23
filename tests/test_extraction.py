"""
Comprehensive Test Suite for Invoice Extraction Engine.
Tests all 7 workspace demo PDFs against manually verified ground truth.
"""
import os
import json
import pytest
from invoice_extractor.engine import InvoiceExtractionEngine

@pytest.fixture(scope="module")
def engine():
    return InvoiceExtractionEngine()

@pytest.fixture(scope="module")
def ground_truth():
    gt_path = os.path.join(os.path.dirname(__file__), "ground_truth.json")
    with open(gt_path, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.mark.parametrize("pdf_name", [
    "64196_20260224.pdf",
    "6934.pdf",
    "GJ03BV6630.pdf",
    "INVOICE3715.pdf",
    "Invoice_VGIL2526ND1798.pdf",
    "SalesBill_GT_5776.PDF",
    "Sales_2775 SANMAT.pdf",
    "highlighted.pdf",
    "wordpress-pdf-invoice-plugin-sample.pdf",
])
def test_invoice_extraction_ground_truth(engine, ground_truth, pdf_name):
    """Verifies that extracted invoice fields match manually verified ground truth."""
    assert os.path.exists(pdf_name), f"File {pdf_name} not found in workspace."
    exp = ground_truth[pdf_name]
    
    response = engine.extract(pdf_name)
    assert response is not None
    inv = response.data
    
    # 1. Invoice Number
    assert inv.invoice.invoice_number == exp["invoice_number"], (
        f"Invoice number mismatch for {pdf_name}: got {inv.invoice.invoice_number}, expected {exp['invoice_number']}"
    )
    
    # 2. Invoice Date
    assert inv.invoice.invoice_date == exp["invoice_date"], (
        f"Invoice date mismatch for {pdf_name}: got {inv.invoice.invoice_date}, expected {exp['invoice_date']}"
    )
    
    # 3. Subtotal
    assert inv.financials.subtotal == pytest.approx(exp["subtotal"], rel=1e-2), (
        f"Subtotal mismatch for {pdf_name}: got {inv.financials.subtotal}, expected {exp['subtotal']}"
    )
    
    # 4. Grand Total
    assert inv.financials.grand_total == pytest.approx(exp["grand_total"], rel=1e-2), (
        f"Grand total mismatch for {pdf_name}: got {inv.financials.grand_total}, expected {exp['grand_total']}"
    )
    
    # 5. Tax Amount
    assert inv.financials.total_tax_amount == pytest.approx(exp["tax_amount"], rel=1e-2), (
        f"Tax amount mismatch for {pdf_name}: got {inv.financials.total_tax_amount}, expected {exp['tax_amount']}"
    )
    
    # 6. Currency
    assert inv.invoice.currency == exp["currency"]
    
    # 7. Line Items
    assert len(inv.line_items) >= 1
    item = inv.line_items[0]
    exp_item = exp["line_items"][0]
    assert item.quantity == pytest.approx(exp_item["quantity"], rel=1e-2)
    assert item.total == pytest.approx(exp_item["total"], rel=1e-2)
    
    # 8. Confidence Score
    assert response.confidence_score >= 0.85

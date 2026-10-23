"""
Validation and Business Rules Engine.
Performs arithmetic cross-validation, date sanity checks, completeness scoring,
and generates structured warnings for discrepancies or missing fields.
"""
from typing import List, Tuple
from invoice_extractor.schema import StandardInvoice


class InvoiceValidator:
    """Validates an extracted StandardInvoice instance against accounting rules."""

    def __init__(self, tolerance: float = 2.0):
        self.tolerance = tolerance

    def validate(self, invoice: StandardInvoice) -> Tuple[List[str], float, bool]:
        """
        Validates invoice data and returns (warnings, calibrated_confidence, needs_review).
        """
        warnings: List[str] = []
        score_deductions = 0.0

        # 1. Mandatory Header Fields Check
        if not invoice.invoice.invoice_number:
            warnings.append("Missing mandatory field: Invoice Number")
            score_deductions += 0.20

        if not invoice.invoice.invoice_date:
            warnings.append("Missing mandatory field: Invoice Date")
            score_deductions += 0.15

        if not invoice.supplier.name:
            warnings.append("Missing mandatory field: Supplier Name")
            score_deductions += 0.15

        if not invoice.customer.name:
            warnings.append("Missing mandatory field: Customer Name")
            score_deductions += 0.10

        if invoice.financials.grand_total is None or invoice.financials.grand_total <= 0:
            warnings.append("Missing or non-positive Grand Total")
            score_deductions += 0.25

        # 2. Date Ambiguity Check
        if invoice.invoice.metadata.get("invoice_date") and invoice.invoice.metadata["invoice_date"].is_ambiguous:
            warnings.append(
                f"Invoice Date '{invoice.invoice.metadata['invoice_date'].raw_value}' is ambiguous (day and month <= 12). Defaulted to DMY."
            )
            score_deductions += 0.05

        # 3. Line Items Validation
        if not invoice.line_items:
            warnings.append("No line items were extracted from the document.")
            score_deductions += 0.15
        else:
            items_total_sum = 0.0
            for idx, item in enumerate(invoice.line_items):
                if item.quantity is not None and item.unit_price is not None:
                    expected_item_total = round(item.quantity * item.unit_price, 2)
                    if abs(expected_item_total - item.total) > self.tolerance:
                        warnings.append(
                            f"Line item {idx+1} arithmetic discrepancy: {item.quantity} * {item.unit_price} = {expected_item_total}, but line total is {item.total}"
                        )
                        score_deductions += 0.05
                items_total_sum += item.total

            if invoice.financials.subtotal is not None:
                if abs(items_total_sum - invoice.financials.subtotal) > self.tolerance:
                    warnings.append(
                        f"Line items sum ({items_total_sum:.2f}) does not match subtotal ({invoice.financials.subtotal:.2f}). Difference: {abs(items_total_sum - invoice.financials.subtotal):.2f}"
                    )
                    score_deductions += 0.10

        # 4. Financial Totals & Tax Arithmetic Check
        subtotal = invoice.financials.subtotal
        grand_total = invoice.financials.grand_total

        if subtotal is not None and grand_total is not None:
            tax_sum = sum(t.amount for t in invoice.financials.tax_components)
            adjustments = invoice.financials.additional_fees or 0.0
            discounts = invoice.financials.total_discounts or 0.0
            shipping = invoice.financials.shipping_charges or 0.0

            calculated_grand_total = round(subtotal + tax_sum + adjustments + shipping - discounts, 2)

            # Check if formula matches with all taxes or just GST components
            discrepancy = abs(calculated_grand_total - grand_total)
            if discrepancy > self.tolerance:
                # Check if grand total equals subtotal + total_tax_amount + adjustments
                gst_total = invoice.financials.total_tax_amount or 0.0
                alt_calculated = round(subtotal + gst_total + adjustments, 2)
                if abs(alt_calculated - grand_total) > self.tolerance:
                    warnings.append(
                        f"Grand total reconciliation note: Reported Grand Total is {grand_total:.2f}, calculated is {calculated_grand_total:.2f} (diff: {discrepancy:.2f})"
                    )
                    score_deductions += 0.05

        confidence = max(0.0, min(1.0, 1.0 - score_deductions))
        needs_review = confidence < 0.80 or any("Missing mandatory" in w for w in warnings)

        return warnings, round(confidence, 2), needs_review

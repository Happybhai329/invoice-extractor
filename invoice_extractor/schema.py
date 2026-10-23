"""
Standard Invoice Schema and Data Models.
Defines Pydantic models for structured invoice representation, metadata tracking,
line items, financial totals, extraction responses, and safe diagnostic observability.
"""
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Bounding box coordinates on a document page (x0, y0, x1, y1)."""
    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 1


class FieldMetadata(BaseModel):
    """Metadata for an extracted field to support auditing, provenance, and validation."""
    raw_value: Optional[str] = None
    normalized_value: Optional[Any] = None
    page: Optional[int] = 1
    bounding_box: Optional[BoundingBox] = None
    source_text: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_ambiguous: bool = False
    is_inferred: bool = False


class TaxComponent(BaseModel):
    """Individual tax breakdown item (e.g. CGST, SGST, IGST, Cess, VAT, TCS, Tax)."""
    tax_type: str = Field(..., description="Type of tax (e.g., CGST, SGST, IGST, CESS, TCS, VAT, Tax)")
    rate: Optional[float] = Field(None, description="Tax rate in percentage (e.g., 2.5 for 2.5%)")
    amount: float = Field(..., description="Monetary tax amount")
    metadata: Optional[FieldMetadata] = None


class LineItem(BaseModel):
    """Invoice line item details."""
    line_number: Optional[int] = Field(None, description="Index or serial number of the item")
    description: str = Field(..., description="Description of the good or service")
    code: Optional[str] = Field(None, description="Item code, SKU, or HSN/SAC code")
    quantity: Optional[float] = Field(None, description="Quantity of units")
    unit_of_measure: Optional[str] = Field(None, description="Unit (e.g., MT, MTS, KG, PCS, Hours)")
    unit_price: Optional[float] = Field(None, description="Unit price / rate")
    discount: Optional[float] = Field(0.0, description="Line item discount amount")
    tax_rate: Optional[float] = Field(None, description="Tax rate applicable to this item")
    tax_amount: Optional[float] = Field(None, description="Tax amount for this line item")
    total: float = Field(..., description="Line item total amount before tax or line total")
    metadata: Optional[FieldMetadata] = None


class SupplierDetails(BaseModel):
    """Supplier / Vendor information."""
    name: Optional[str] = Field(None, description="Supplier / Vendor legal or trading name")
    address: Optional[str] = Field(None, description="Supplier physical or registered address")
    tax_id: Optional[str] = Field(None, description="Supplier Tax ID, GSTIN, VAT ID, or PAN")
    email: Optional[str] = Field(None, description="Supplier email address")
    phone: Optional[str] = Field(None, description="Supplier phone / telephone number")
    website: Optional[str] = Field(None, description="Supplier website")
    metadata: Dict[str, FieldMetadata] = Field(default_factory=dict)


class CustomerDetails(BaseModel):
    """Customer / Buyer information."""
    name: Optional[str] = Field(None, description="Customer / Buyer legal or trading name")
    billing_address: Optional[str] = Field(None, description="Billing address")
    shipping_address: Optional[str] = Field(None, description="Shipping or delivery address if different")
    tax_id: Optional[str] = Field(None, description="Customer Tax ID, GSTIN, VAT ID, or PAN")
    email: Optional[str] = Field(None, description="Customer email address")
    phone: Optional[str] = Field(None, description="Customer phone number")
    metadata: Dict[str, FieldMetadata] = Field(default_factory=dict)


class InvoiceDetails(BaseModel):
    """Invoice metadata and core header fields."""
    invoice_number: Optional[str] = Field(None, description="Unique invoice identifier/number")
    invoice_date: Optional[str] = Field(None, description="Date of invoice issue in ISO 8601 (YYYY-MM-DD)")
    due_date: Optional[str] = Field(None, description="Payment due date in ISO 8601 (YYYY-MM-DD)")
    purchase_order_number: Optional[str] = Field(None, description="Associated Purchase Order (PO) number")
    payment_terms: Optional[str] = Field(None, description="Payment terms or conditions (e.g., 7 Days, Net 30)")
    currency: Optional[str] = Field("INR", description="Standard 3-letter currency code (e.g. INR, USD, EUR)")
    document_type: Optional[str] = Field("TAX INVOICE", description="Type of document (e.g. Tax Invoice, Debit Memo)")
    metadata: Dict[str, FieldMetadata] = Field(default_factory=dict)


class FinancialDetails(BaseModel):
    """Financial breakdown and totals."""
    subtotal: Optional[float] = Field(None, description="Net amount / subtotal before taxes and adjustments")
    total_discounts: Optional[float] = Field(0.0, description="Total discount applied at document level")
    shipping_charges: Optional[float] = Field(0.0, description="Freight or shipping charges")
    additional_fees: Optional[float] = Field(0.0, description="Other fees (e.g. round off, convenience fee)")
    taxable_amount: Optional[float] = Field(None, description="Total taxable amount subject to tax")
    tax_components: List[TaxComponent] = Field(default_factory=list, description="Itemized tax components")
    total_tax_amount: Optional[float] = Field(None, description="Sum of all tax components")
    total_before_tax: Optional[float] = Field(None, description="Total amount before tax")
    grand_total: Optional[float] = Field(None, description="Final payable invoice amount")
    amount_paid: Optional[float] = Field(0.0, description="Amount already paid if indicated")
    outstanding_balance: Optional[float] = Field(None, description="Net outstanding balance due")
    metadata: Dict[str, FieldMetadata] = Field(default_factory=dict)


class StandardInvoice(BaseModel):
    """Standardized invoice model capturing complete normalized invoice data."""
    supplier: SupplierDetails = Field(default_factory=SupplierDetails)
    customer: CustomerDetails = Field(default_factory=CustomerDetails)
    invoice: InvoiceDetails = Field(default_factory=InvoiceDetails)
    financials: FinancialDetails = Field(default_factory=FinancialDetails)
    line_items: List[LineItem] = Field(default_factory=list)


class ExtractionResponse(BaseModel):
    """Standard API extraction response payload."""
    data: StandardInvoice
    raw_text: Optional[str] = None
    extraction_method: str = Field(default="hybrid", description="embedded_text, ocr, or ai_assisted")
    page_count: int = 1
    warnings: List[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    diagnostics: Dict[str, Any] = Field(default_factory=dict, description="Safe developer diagnostics")

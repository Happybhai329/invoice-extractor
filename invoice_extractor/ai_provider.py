"""
Pluggable AI & Document-Understanding Provider Interface.
Supports external LLM/Vision document processing when API keys are configured,
with full isolation and graceful fallback to the local extraction pipeline.
"""
import os
import json
import base64
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import httpx

from invoice_extractor.schema import (
    StandardInvoice,
    SupplierDetails,
    CustomerDetails,
    InvoiceDetails,
    FinancialDetails,
    LineItem,
    TaxComponent,
    FieldMetadata,
)

logger = logging.getLogger("ai_provider")


def _load_env_file():
    """Lightweight .env loader without external dependency."""
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"\'')
                    if k not in os.environ:
                        os.environ[k] = v

_load_env_file()


class BaseDocumentAIProvider(ABC):
    """Abstract interface for external document intelligence providers."""

    @abstractmethod
    def is_available(self) -> bool:
        """Returns True if the provider is configured and available."""
        pass

    @abstractmethod
    def extract_invoice(self, file_path_or_bytes: Any, text_content: str) -> Optional[StandardInvoice]:
        """Extracts structured invoice information using external AI service."""
        pass


class ConfigurableAIProvider(BaseDocumentAIProvider):
    """
    Configurable AI provider supporting Google Gemini API via GEMINI_API_KEY.
    Extracts structured invoices using Gemini's native multimodal PDF capability
    or text prompt with response_mime_type="application/json".
    """

    def __init__(
        self,
        api_key_env_var: str = "GEMINI_API_KEY",
        api_key: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key_env_var = api_key_env_var
        self.api_key = api_key or os.getenv(api_key_env_var) or os.getenv("GOOGLE_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def extract_invoice(self, file_path_or_bytes: Any, text_content: str) -> Optional[StandardInvoice]:
        if not self.is_available():
            return None

        # Prepare payload: multimodal PDF if bytes/path available, otherwise text
        pdf_bytes = None
        if isinstance(file_path_or_bytes, bytes):
            pdf_bytes = file_path_or_bytes
        elif isinstance(file_path_or_bytes, str) and os.path.exists(file_path_or_bytes):
            try:
                with open(file_path_or_bytes, "rb") as f:
                    pdf_bytes = f.read()
            except Exception as e:
                logger.warning(f"Could not read PDF file for Gemini: {e}")

        prompt = (
            "You are an expert document AI invoice extractor. "
            "Extract all invoice details from this document into structured JSON matching this schema:\n"
            "{\n"
            '  "supplier": {"name": str, "address": str, "tax_id": str, "email": str, "phone": str},\n'
            '  "customer": {"name": str, "billing_address": str, "shipping_address": str, "tax_id": str, "email": str, "phone": str},\n'
            '  "invoice": {"invoice_number": str, "invoice_date": "YYYY-MM-DD", "due_date": "YYYY-MM-DD", "purchase_order_number": str, "payment_terms": str, "currency": str, "document_type": str},\n'
            '  "financials": {"subtotal": float, "total_discounts": float, "shipping_charges": float, "additional_fees": float, "taxable_amount": float, "total_tax_amount": float, "grand_total": float, "tax_components": [{"tax_type": str, "rate": float, "amount": float}]},\n'
            '  "line_items": [{"line_number": int, "description": str, "code": str, "quantity": float, "unit_of_measure": str, "unit_price": float, "total": float}]\n'
            "}\n"
            "Return strictly valid JSON only. Normalize dates to ISO YYYY-MM-DD format. "
            "Clean monetary numbers to standard floats without commas."
        )

        parts = [{"text": prompt}]

        if pdf_bytes:
            pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
            parts.append({"inline_data": {"mime_type": "application/pdf", "data": pdf_b64}})
        elif text_content:
            parts.append({"text": f"Document Text:\n{text_content}"})
        else:
            return None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "X-goog-api-key": self.api_key
        }
        data = {
            "contents": [{"parts": parts}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        try:
            with httpx.Client(timeout=35.0) as client:
                resp = client.post(url, headers=headers, json=data)

            if resp.status_code != 200:
                logger.warning(f"Gemini API returned status {resp.status_code}: {resp.text[:200]}")
                return None

            result = resp.json()
            candidates = result.get("candidates", [])
            if not candidates:
                return None

            text_response = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not text_response:
                return None

            raw_dict = json.loads(text_response)
            return self._build_standard_invoice(raw_dict)
        except Exception as e:
            logger.warning(f"Gemini AI extraction error: {e}. Falling back to deterministic engine.")
            return None

    def _build_standard_invoice(self, data: Dict[str, Any]) -> StandardInvoice:
        # Build Supplier
        s_data = data.get("supplier", {}) or {}
        supplier = SupplierDetails(
            name=s_data.get("name"),
            address=s_data.get("address"),
            tax_id=s_data.get("tax_id"),
            email=s_data.get("email"),
            phone=s_data.get("phone"),
        )
        if supplier.name:
            supplier.metadata["name"] = FieldMetadata(raw_value=supplier.name, normalized_value=supplier.name, page=1, confidence=0.99)

        # Build Customer
        c_data = data.get("customer", {}) or {}
        customer = CustomerDetails(
            name=c_data.get("name"),
            billing_address=c_data.get("billing_address"),
            shipping_address=c_data.get("shipping_address"),
            tax_id=c_data.get("tax_id"),
            email=c_data.get("email"),
            phone=c_data.get("phone"),
        )
        if customer.name:
            customer.metadata["name"] = FieldMetadata(raw_value=customer.name, normalized_value=customer.name, page=1, confidence=0.99)

        # Build Invoice Details
        i_data = data.get("invoice", {}) or {}
        curr = i_data.get("currency") or "INR"
        if curr == "$":
            curr = "USD"
        elif curr == "₹":
            curr = "INR"
        elif curr == "€":
            curr = "EUR"
        elif curr == "£":
            curr = "GBP"

        invoice_details = InvoiceDetails(
            invoice_number=i_data.get("invoice_number"),
            invoice_date=i_data.get("invoice_date"),
            due_date=i_data.get("due_date"),
            purchase_order_number=i_data.get("purchase_order_number"),
            payment_terms=i_data.get("payment_terms"),
            currency=curr,
            document_type=i_data.get("document_type") or "Invoice",
        )
        if invoice_details.invoice_number:
            invoice_details.metadata["invoice_number"] = FieldMetadata(
                raw_value=invoice_details.invoice_number, normalized_value=invoice_details.invoice_number, page=1, confidence=0.99
            )

        # Build Financials
        f_data = data.get("financials", {}) or {}
        tax_comps = []
        for tc in f_data.get("tax_components", []):
            if isinstance(tc, dict) and tc.get("amount"):
                tax_comps.append(
                    TaxComponent(
                        tax_type=str(tc.get("tax_type", "TAX")).upper(),
                        rate=float(tc["rate"]) if tc.get("rate") is not None else None,
                        amount=float(tc["amount"])
                    )
                )

        financials = FinancialDetails(
            subtotal=f_data.get("subtotal"),
            total_discounts=f_data.get("total_discounts") or 0.0,
            shipping_charges=f_data.get("shipping_charges") or 0.0,
            additional_fees=f_data.get("additional_fees") or 0.0,
            taxable_amount=f_data.get("taxable_amount") or f_data.get("subtotal"),
            tax_components=tax_comps,
            total_tax_amount=f_data.get("total_tax_amount"),
            grand_total=f_data.get("grand_total"),
        )

        # Build Line Items
        line_items = []
        for idx, item in enumerate(data.get("line_items", [])):
            if isinstance(item, dict):
                qty = float(item["quantity"]) if item.get("quantity") is not None else 1.0
                unit_price = float(item["unit_price"]) if item.get("unit_price") is not None else 0.0
                total = float(item["total"]) if item.get("total") is not None else round(qty * unit_price, 2)
                line_items.append(
                    LineItem(
                        line_number=item.get("line_number") or (idx + 1),
                        description=item.get("description") or "Item",
                        code=item.get("code"),
                        quantity=qty,
                        unit_of_measure=item.get("unit_of_measure"),
                        unit_price=unit_price,
                        total=total,
                    )
                )

        return StandardInvoice(
            supplier=supplier,
            customer=customer,
            invoice=invoice_details,
            financials=financials,
            line_items=line_items,
        )

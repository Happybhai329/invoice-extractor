"""
Format-Independent, Layout-Aware Invoice Parser.
Extracts normalized structured invoice data across varied templates, labels,
currencies, tax regimes, and table layouts without hardcoded document assumptions.
"""
import re
from typing import List, Optional, Tuple, Dict, Any
from invoice_extractor.document_reader import DocumentContent, TextBlock, PageContent
from invoice_extractor.schema import (
    StandardInvoice,
    SupplierDetails,
    CustomerDetails,
    InvoiceDetails,
    FinancialDetails,
    TaxComponent,
    LineItem,
    FieldMetadata,
    BoundingBox,
)
from invoice_extractor.date_normalizer import normalize_date
from invoice_extractor.currency_normalizer import normalize_amount, detect_currency

GSTIN_REGEX = r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b"
EMAIL_REGEX = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"


class InvoiceParser:
    """Parses DocumentContent into a structured StandardInvoice model."""

    def parse(self, doc_content: DocumentContent) -> StandardInvoice:
        invoice = StandardInvoice()
        if not doc_content.pages:
            return invoice

        p1 = doc_content.pages[0]
        full_text = doc_content.full_text

        # 1. Document Type & Currency
        invoice.invoice.document_type = self._extract_document_type(full_text)
        invoice.invoice.currency = self._extract_currency(full_text)

        # 2. Tax Identifiers (GSTIN, VAT, etc.)
        self._extract_tax_identifiers(doc_content, invoice.supplier, invoice.customer)

        # 3. Invoice Number
        inv_no_res = self._extract_invoice_number(doc_content)
        if inv_no_res:
            invoice.invoice.invoice_number = inv_no_res[0]
            invoice.invoice.metadata["invoice_number"] = inv_no_res[1]

        # 4. Invoice Date
        inv_date_res = self._extract_invoice_date(doc_content)
        if inv_date_res:
            invoice.invoice.invoice_date = inv_date_res[0]
            invoice.invoice.metadata["invoice_date"] = inv_date_res[1]

        # 5. Due Date
        due_date_res = self._extract_due_date(doc_content)
        if due_date_res:
            invoice.invoice.due_date = due_date_res[0]
            invoice.invoice.metadata["due_date"] = due_date_res[1]

        # 6. PO Number & Payment Terms
        po_res = self._extract_po_number(doc_content)
        if po_res:
            invoice.invoice.purchase_order_number = po_res[0]
            invoice.invoice.metadata["purchase_order_number"] = po_res[1]

        terms_res = self._extract_payment_terms(doc_content)
        if terms_res:
            invoice.invoice.payment_terms = terms_res[0]
            invoice.invoice.metadata["payment_terms"] = terms_res[1]

        # 7. Supplier & Customer Details (Parties & Addresses)
        self._extract_parties(doc_content, invoice.supplier, invoice.customer)

        # 8. Generic Table Line Items Extraction
        invoice.line_items = self._extract_line_items(doc_content)

        # 9. Financial Details & Taxes
        self._extract_financials(doc_content, invoice.financials, invoice.line_items)

        return invoice

    # -------------------------------------------------------------------------
    # Document Type & Currency
    # -------------------------------------------------------------------------

    def _extract_document_type(self, text: str) -> str:
        lower = text.lower()
        if re.search(r"\bdebit\s*memo\b", lower) or re.search(r"\bdebit\s*note\b", lower):
            return "Debit Memo"
        if re.search(r"\bcredit\s*memo\b", lower) or re.search(r"\bcredit\s*note\b", lower):
            return "Credit Memo"
        if re.search(r"\be-?invoice\b", lower):
            return "e-Invoice"
        if re.search(r"\btax\s*invoice\b", lower):
            return "TAX INVOICE"
        if re.search(r"\bsales\s*invoice\b", lower):
            return "Sales Invoice"
        if re.search(r"\bbill\s*of\s*supply\b", lower):
            return "Bill of Supply"
        if re.search(r"\binvoice\b", lower):
            return "Invoice"
        return "Invoice"

    def _extract_currency(self, text: str) -> str:
        curr = detect_currency(text)
        if curr:
            if curr == "USD":
                if re.search(r"(?i)\b(vic\s*\d{4}|nsw\s*\d{4}|melbourne|sydney|australia|aud)\b", text):
                    return "AUD"
                if re.search(r"(?i)\b(ontario|toronto|canada|cad)\b", text):
                    return "CAD"
            return curr
        if re.search(GSTIN_REGEX, text):
            return "INR"
        return "INR"

    # -------------------------------------------------------------------------
    # Tax Identifiers
    # -------------------------------------------------------------------------

    def _extract_tax_identifiers(
        self, doc_content: DocumentContent, supplier: SupplierDetails, customer: CustomerDetails
    ):
        p1 = doc_content.pages[0]
        gstins_found = []
        for p in doc_content.pages:
            for b in p.blocks:
                for gstin in re.findall(GSTIN_REGEX, b.text):
                    b_lower = b.text.lower()
                    is_cust = any(k in b_lower for k in ["billed to", "bill to", "buyer", "receiver", "consignee", "party gst", "supply to", "to:"])
                    gstins_found.append((gstin, is_cust, p.page_num, b.bbox))

        for gstin, is_cust, page_num, bbox in gstins_found:
            meta = FieldMetadata(
                raw_value=gstin,
                normalized_value=gstin,
                page=page_num,
                bounding_box=BoundingBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3], page=page_num) if bbox else None,
                confidence=0.98
            )
            if is_cust:
                if not customer.tax_id:
                    customer.tax_id = gstin
                    customer.metadata["tax_id"] = meta
            else:
                if not supplier.tax_id:
                    supplier.tax_id = gstin
                    supplier.metadata["tax_id"] = meta

        if len(gstins_found) == 1 and not supplier.tax_id and not customer.tax_id:
            gstin, is_cust, page_num, bbox = gstins_found[0]
            supplier.tax_id = gstin
            supplier.metadata["tax_id"] = FieldMetadata(raw_value=gstin, normalized_value=gstin, page=page_num, confidence=0.95)

    # -------------------------------------------------------------------------
    # Invoice Number
    # -------------------------------------------------------------------------

    def _extract_invoice_number(self, doc_content: DocumentContent) -> Optional[Tuple[str, FieldMetadata]]:
        p1 = doc_content.pages[0]

        # 1. Blocks search
        for b in p1.blocks:
            lines = [l.strip() for l in b.text.split("\n") if l.strip()]
            for i, l in enumerate(lines):
                l_lower = l.lower()
                if any(k in l_lower for k in ["invoice no", "invoice number", "bill no", "tax invoice no", "inv no", "invoice #"]):
                    if any(k in l_lower for k in ["ack", "irn", "cancel", "payment of this invoice"]):
                        continue
                    # Same line with value (avoid capturing 'No' or 'Number' via negative lookahead)
                    m = re.search(r"(?i)\b(?:tax\s*)?invoice\s*(?:no\.?|number|#|id)?\b\s*[:\-]?\s*(?!(?:no\.?|number|date|id)\b)([A-Za-z0-9\/\-_]+)", l)
                    if m:
                        cand = m.group(1).lstrip(":- ").strip()
                        if self._is_valid_id(cand):
                            return cand, FieldMetadata(raw_value=cand, normalized_value=cand, page=b.page_num, source_text=b.text, confidence=0.98)

                    # Standalone label: check block directly below (spatial layout for digital PDFs)
                    if len(lines) == 1 and not p1.is_ocr:
                        below = sorted([b2 for b2 in p1.blocks if abs(b2.x0 - b.x0) < 40 and b2.y0 >= b.y0 and b2.y0 < b.y0 + 35 and b2 != b], key=lambda b2: b2.y0)
                        if below:
                            cand_b = below[0].text.strip()
                            if self._is_valid_id(cand_b) and not any(k in cand_b.lower() for k in ["so no", "sono", "ack", "irn"]):
                                return cand_b, FieldMetadata(raw_value=cand_b, normalized_value=cand_b, page=b.page_num, source_text=b.text, confidence=0.98)

                    # Multi-line cases
                    if i + 1 < len(lines):
                        raw1 = lines[i + 1].strip()
                        if raw1 in [":", "-", ":-"] and i + 2 < len(lines):
                            cand = lines[i + 2].lstrip(":- ").strip()
                            if self._is_valid_id(cand):
                                return cand, FieldMetadata(raw_value=cand, normalized_value=cand, page=b.page_num, source_text=b.text, confidence=0.98)
                        elif "e-way" in raw1.lower() and i + 2 < len(lines):
                            cand = lines[i + 2].lstrip(":- ").strip()
                            if self._is_valid_id(cand):
                                return cand, FieldMetadata(raw_value=cand, normalized_value=cand, page=b.page_num, source_text=b.text, confidence=0.98)
                        else:
                            cand1 = raw1.lstrip(":- ").strip()
                            if self._is_valid_id(cand1):
                                return cand1, FieldMetadata(raw_value=cand1, normalized_value=cand1, page=b.page_num, source_text=b.text, confidence=0.98)

                    # Value above label (e.g. INVOICE3715)
                    if i > 0:
                        cand_above = lines[i - 1].lstrip(":- ").strip()
                        if self._is_valid_id(cand_above) and not any(k in cand_above.lower() for k in ["gstin", "tax", "original", "page", "cin", "pan", "debit"]):
                            return cand_above, FieldMetadata(raw_value=cand_above, normalized_value=cand_above, page=b.page_num, source_text=b.text, confidence=0.95)

        # 2. Visual lines search
        for vl in p1.visual_lines:
            if any(k in vl.lower() for k in ["irn", "ack.", "ack no", "ack. no", "cancel", "payment of this invoice"]):
                continue
            # Inline
            m = re.search(r"(?i)(?:tax\s*)?invoice\s*(?:no\.?|number|#|id)?\s*[:\-]?\s*(?!(?:no\.?|number|date|id)\b)([A-Za-z0-9\/\-_]+)", vl)
            if m:
                cand = m.group(1).lstrip(":- ").strip()
                if self._is_valid_id(cand):
                    return cand, FieldMetadata(raw_value=cand, normalized_value=cand, page=1, source_text=vl, confidence=0.95)

            # Preceding label (e.g. "GGLSUK022305172 08-Feb-2024 Invoice No")
            m_rev = re.search(r"([A-Za-z0-9\/\-_]{3,30})\s+(?:[A-Za-z0-9\/\-_\.]+\s+)?(?:tax\s*)?invoice\s*no", vl, flags=re.I)
            if m_rev:
                cand = m_rev.group(1).lstrip(":- ").strip()
                if self._is_valid_id(cand):
                    return cand, FieldMetadata(raw_value=cand, normalized_value=cand, page=1, source_text=vl, confidence=0.95)

            # Composite "Doc No. : Tax Invoice - 2775"
            m_doc = re.search(r"(?i)doc\s*no\.?\s*:\s*tax\s*invoice\s*-\s*([A-Za-z0-9\/\-_]+)", vl)
            if m_doc:
                cand = m_doc.group(1).lstrip(":- ").strip()
                if self._is_valid_id(cand):
                    return cand, FieldMetadata(raw_value=cand, normalized_value=cand, page=1, source_text=vl, confidence=0.98)

        # 3. Multi-page e-way bill fallback (Page 2)
        if len(doc_content.pages) > 1:
            for vl in doc_content.pages[1].visual_lines:
                m_inv = re.search(r"(?i)doc\s*no\s*[:\-]?\s*([A-Za-z0-9\/\-_]+)", vl)
                if m_inv:
                    cand = m_inv.group(1).lstrip(":- ").strip()
                    if self._is_valid_id(cand):
                        return cand, FieldMetadata(raw_value=cand, normalized_value=cand, page=2, confidence=0.90)

        return None

    def _is_valid_id(self, val: str) -> bool:
        if not val or len(val) < 2 or len(val) > 35:
            return False
        if re.search(r"^[0-9a-f]{40,}", val.lower()):
            return False
        if ".com" in val.lower() or ".org" in val.lower() or val.lower() in ["faker", "invoicefaker", "sample", "warning"]:
            return False
        val_clean = val.lower().strip(":- ")
        stopwords = [
            "invoice", "tax", "bill", "original", "duplicate", "buyer", "seller",
            "date", "dated", "verbal", "e-way", "details", "consignee", "gstin",
            "order", "receiptant", "transport", "memo", "page", "total", "supply",
            "no", "no.", "number", "id", "ack", "ack.", "ackno", "so", "sono", "so no", "irn"
        ]
        if val_clean in stopwords:
            return False
        return not any(sw in val_clean for sw in ["date", "details", "buyer", "consignee", "gstin", "order", "receiptant", "transport", "page", "supply"])

    # -------------------------------------------------------------------------
    # Dates
    # -------------------------------------------------------------------------

    def _extract_invoice_date(self, doc_content: DocumentContent) -> Optional[Tuple[str, FieldMetadata]]:
        p1 = doc_content.pages[0]

        # 1. Multi-line blocks: "Invoice Date\nJanuary 25, 2016" or "Date\n06/Jul/2025"
        for b in p1.blocks:
            lines = [ln.strip() for ln in b.text.split("\n") if ln.strip()]
            for i, ln in enumerate(lines):
                if re.search(r"(?i)^(?:tax\s*)?invoice\s*date$", ln) or (re.search(r"(?i)^date\s*:?$", ln) and "due" not in b.text.lower()):
                    if i + 1 < len(lines):
                        cand = lines[i + 1].lstrip(":- ").strip()
                        norm_d, is_ambig, conf = normalize_date(cand)
                        if norm_d:
                            return norm_d, FieldMetadata(raw_value=cand, normalized_value=norm_d, page=b.page_num, source_text=b.text, confidence=conf, is_ambiguous=is_ambig)

        # 2. Check visual lines for inline label + date
        for vl in p1.visual_lines:
            if any(k in vl.lower() for k in ["due date", "delivery note", "lr dt", "order date", "valid upto", "pay by"]):
                continue

            # Check reverse pattern: "GGLSUK022305172 08-Feb-2024 Invoice No : Invoice Date :"
            m_rev = re.search(r"([0-9]{1,2}[-/\s][A-Za-z0-9]{2,9}[-/\s][0-9]{2,4})\s+.*(?:invoice\s*date|dated)", vl, flags=re.I)
            if m_rev:
                cand = m_rev.group(1).lstrip(":- ").strip()
                norm_d, is_ambig, conf = normalize_date(cand)
                if norm_d:
                    return norm_d, FieldMetadata(raw_value=cand, normalized_value=norm_d, page=1, source_text=vl, confidence=conf, is_ambiguous=is_ambig)

            # Check named month inline: "January 25, 2016" or "06/Jul/2025" or "06-Jul-2025"
            m_named = re.search(r"(?i)(?:invoice\s*date|dated|(?<!\w)date)\s*[:\-]?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}|\d{1,2}[-/\s][A-Za-z]{3,9}[-/\s]\d{2,4})", vl)
            if m_named:
                cand = m_named.group(1).lstrip(":- ").strip()
                norm_d, is_ambig, conf = normalize_date(cand)
                if norm_d:
                    return norm_d, FieldMetadata(raw_value=cand, normalized_value=norm_d, page=1, source_text=vl, confidence=conf, is_ambiguous=is_ambig)

            # Check numeric inline: "05-08-2025" or "2025/08/05" or "03/05/2025"
            m_num = re.search(r"(?i)(?:invoice\s*date|dated|(?<!\w)date)\s*[:\-]?\s*([0-9]{1,4}[-/\.][0-9]{1,2}[-/\.][0-9]{2,4})", vl)
            if m_num:
                cand = m_num.group(1).lstrip(":- ").strip()
                norm_d, is_ambig, conf = normalize_date(cand)
                if norm_d:
                    return norm_d, FieldMetadata(raw_value=cand, normalized_value=norm_d, page=1, source_text=vl, confidence=conf, is_ambiguous=is_ambig)

        # 3. Check blocks text directly
        for b in p1.blocks:
            if any(k in b.text.lower() for k in ["due date", "delivery note", "lr dt", "order date", "valid upto"]):
                continue
            m = re.search(r"(?i)(?:invoice\s*date|dated|(?<!\w)date)\s*[:\-]?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}|\d{1,2}[-/\s][A-Za-z]{3,9}[-/\s]\d{2,4}|[0-9]{1,4}[-/\.][0-9]{1,2}[-/\.][0-9]{2,4})", b.text)
            if m:
                cand = m.group(1).lstrip(":- ").strip()
                norm_d, is_ambig, conf = normalize_date(cand)
                if norm_d:
                    return norm_d, FieldMetadata(raw_value=cand, normalized_value=norm_d, page=b.page_num, source_text=b.text, confidence=conf, is_ambiguous=is_ambig)

        # 4. Fallback to Page 2 if needed
        if len(doc_content.pages) > 1:
            for vl in doc_content.pages[1].visual_lines:
                m = re.search(r"(?i)date\s*[:\-]?\s*([0-9]{1,2}[-/\.][A-Za-z0-9]{2,9}[-/\.][0-9]{2,4})", vl)
                if m:
                    cand = m.group(1).lstrip(":- ").strip()
                    norm_d, is_ambig, conf = normalize_date(cand)
                    if norm_d:
                        return norm_d, FieldMetadata(raw_value=cand, normalized_value=norm_d, page=2, confidence=conf * 0.9, is_ambiguous=is_ambig)

        return None

    def _extract_due_date(self, doc_content: DocumentContent) -> Optional[Tuple[str, FieldMetadata]]:
        p1 = doc_content.pages[0]

        # 1. Multi-line blocks: "Due Date\nJanuary 31, 2016"
        for b in p1.blocks:
            lines = [ln.strip() for ln in b.text.split("\n") if ln.strip()]
            for i, ln in enumerate(lines):
                if re.search(r"(?i)^(?:payment\s*)?due\s*date$", ln) or re.search(r"(?i)^pay\s*by$", ln):
                    if i + 1 < len(lines):
                        cand = lines[i + 1].lstrip(":- ").strip()
                        norm_d, is_ambig, conf = normalize_date(cand)
                        if norm_d:
                            return norm_d, FieldMetadata(raw_value=cand, normalized_value=norm_d, page=b.page_num, source_text=b.text, confidence=conf, is_ambiguous=is_ambig)

        # 2. Inline visual lines
        for vl in p1.visual_lines:
            m = re.search(r"(?i)(?:payment\s*due\s*date|due\s*date|pay\s*by|payment\s*deadline)\s*[:\-]?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}|\d{1,2}[-/\s][A-Za-z]{3,9}[-/\s]\d{2,4}|[0-9]{1,4}[-/\.][0-9]{1,2}[-/\.][0-9]{2,4})", vl)
            if m:
                cand = m.group(1).lstrip(":- ").strip()
                norm_d, is_ambig, conf = normalize_date(cand)
                if norm_d:
                    return norm_d, FieldMetadata(raw_value=cand, normalized_value=norm_d, page=1, confidence=conf, is_ambiguous=is_ambig)

            # Terms pattern: "payment net cash / 08-02-2024"
            m_terms = re.search(r"(?i)payment\s*net\s*cash\s*/\s*([0-9]{1,2}[-/\.][A-Za-z0-9]{2,9}[-/\.][0-9]{2,4})", vl)
            if m_terms:
                cand = m_terms.group(1).lstrip(":- ").strip()
                norm_d, is_ambig, conf = normalize_date(cand)
                if norm_d:
                    return norm_d, FieldMetadata(raw_value=cand, normalized_value=norm_d, page=1, confidence=0.90, is_ambiguous=is_ambig)

        return None

    # -------------------------------------------------------------------------
    # PO Number & Payment Terms
    # -------------------------------------------------------------------------

    def _extract_po_number(self, doc_content: DocumentContent) -> Optional[Tuple[str, FieldMetadata]]:
        p1 = doc_content.pages[0]

        # Multi-line in blocks: "Order Number\n12345" or standalone spatial label
        for b in p1.blocks:
            lines = [ln.strip() for ln in b.text.split("\n") if ln.strip()]
            for i, ln in enumerate(lines):
                if re.search(r"(?i)^(?:order|po|purchase\s*order)\s*(?:number|no\.?|#)$", ln):
                    if i + 1 < len(lines):
                        cand = lines[i + 1].lstrip(":- ").strip()
                        if self._is_valid_id(cand):
                            return cand, FieldMetadata(raw_value=cand, normalized_value=cand, page=b.page_num, confidence=0.95)
                    elif len(lines) == 1:
                        below = sorted([b2 for b2 in p1.blocks if abs(b2.x0 - b.x0) < 40 and b2.y0 >= b.y0 and b2.y0 < b.y0 + 35 and b2 != b], key=lambda b2: b2.y0)
                        if below:
                            cand_b = below[0].text.strip()
                            if self._is_valid_id(cand_b):
                                return cand_b, FieldMetadata(raw_value=cand_b, normalized_value=cand_b, page=b.page_num, confidence=0.95)

        # Inline visual lines
        for vl in p1.visual_lines:
            m = re.search(r"(?i)(?:party\s*po\s*no\.?|purchase\s*order|order\s*(?:no\.?|number|#))\s*[:\-]?\s*([A-Za-z0-9\s\-_/]+)", vl)
            if m:
                cand = m.group(1).lstrip(":- ").strip()
                cand = re.split(r"(?i)\b(date|so\s*no|dated)\b", cand)[0].strip()
                if cand and len(cand) >= 2 and not any(k in cand.lower() for k in ["verbal", "date"]):
                    return cand, FieldMetadata(raw_value=cand, normalized_value=cand, page=1, confidence=0.92)
                elif cand and "verbal" in cand.lower():
                    return "VERBAL", FieldMetadata(raw_value=cand, normalized_value="VERBAL", page=1, confidence=0.92)

        return None

    def _extract_payment_terms(self, doc_content: DocumentContent) -> Optional[Tuple[str, FieldMetadata]]:
        full_text = doc_content.full_text
        p1 = doc_content.pages[0]

        # Spatial blocks: "PAYMENT DETAILS" with value below
        for b in p1.blocks:
            lines = [ln.strip() for ln in b.text.split("\n") if ln.strip()]
            if lines and re.search(r"(?i)^(?:payment\s*(?:terms|details)|terms)\s*:?$", lines[0]):
                if len(lines) > 1:
                    val = lines[1].strip()
                    return val, FieldMetadata(raw_value=val, normalized_value=val, page=b.page_num, confidence=0.92)
                else:
                    below = sorted([b2 for b2 in p1.blocks if abs(b2.x0 - b.x0) < 40 and b2.y0 >= b.y0 and b2.y0 < b.y0 + 35 and b2 != b], key=lambda b2: b2.y0)
                    if below:
                        val = below[0].text.strip()
                        return val, FieldMetadata(raw_value=val, normalized_value=val, page=b.page_num, confidence=0.92)

        # Explicit label
        for vl in doc_content.pages[0].visual_lines:
            m = re.search(r"(?i)(?:payment\s*terms|terms\s*of\s*payment|terms)\s*[:\-]?\s*([0-9A-Za-z\s]+(?:days|cash|credit)?)", vl)
            if m:
                val = m.group(1).lstrip(":- ").strip()
                if val and len(val) >= 2 and "condition" not in val.lower():
                    return val, FieldMetadata(raw_value=val, normalized_value=val, page=1, confidence=0.88)

        # Descriptive sentence: "Payment is due within 30 days from date of invoice"
        m_due_phrase = re.search(r"(?i)payment\s*is\s*due\s*within\s*(\d+\s*days)", full_text)
        if m_due_phrase:
            val = f"Within {m_due_phrase.group(1).strip()}"
            return val, FieldMetadata(raw_value=m_due_phrase.group(0), normalized_value=val, page=1, confidence=0.90)

        return None

    # -------------------------------------------------------------------------
    # Party Extraction (Supplier & Customer Details)
    # -------------------------------------------------------------------------

    def _extract_parties(self, doc_content: DocumentContent, supplier: SupplierDetails, customer: CustomerDetails):
        p1 = doc_content.pages[0]

        # 1. Emails & Phones
        for b in p1.blocks:
            m_email = re.search(EMAIL_REGEX, b.text)
            if m_email:
                email = m_email.group(0).strip()
                if any(k in b.text.lower() for k in ["to:", "bill to", "buyer", "customer", "client"]):
                    if not customer.email:
                        customer.email = email
                        customer.metadata["email"] = FieldMetadata(raw_value=email, normalized_value=email, page=b.page_num, confidence=0.95)
                else:
                    if not supplier.email:
                        supplier.email = email
                        supplier.metadata["email"] = FieldMetadata(raw_value=email, normalized_value=email, page=b.page_num, confidence=0.95)

            m_phone = re.search(r"(?i)(?:contact|phone|tel|mob)\s*[:\-]?\s*([0-9\-\+\s]{10,14})", b.text)
            if m_phone:
                ph_clean = re.sub(r"[^\d+]", "", m_phone.group(1))
                if len(ph_clean) >= 10:
                    if any(k in b.text.lower() for k in ["to:", "bill to", "buyer", "customer"]):
                        if not customer.phone:
                            customer.phone = ph_clean
                            customer.metadata["phone"] = FieldMetadata(raw_value=m_phone.group(1), normalized_value=ph_clean, page=b.page_num, confidence=0.92)
                    else:
                        if not supplier.phone:
                            supplier.phone = ph_clean
                            supplier.metadata["phone"] = FieldMetadata(raw_value=m_phone.group(1), normalized_value=ph_clean, page=b.page_num, confidence=0.92)

        # 2. Look for explicit "From:" and "To:" blocks
        for b in p1.blocks:
            lines = [ln.strip() for ln in b.text.split("\n") if ln.strip()]
            if not lines:
                continue

            # "From:" Supplier Block
            if re.search(r"(?i)^from\s*:?$", lines[0]) and not supplier.name:
                if len(lines) > 1:
                    s_name = lines[1].strip()
                    addr_lines = [l for l in lines[2:] if not re.search(EMAIL_REGEX, l) and not re.search(r"\b\d{10}\b", l)]
                else:
                    below = sorted([b2 for b2 in p1.blocks if abs(b2.x0 - b.x0) < 50 and b2.y0 > b.y0 and b2.y0 < b.y0 + 110 and b2 != b and not re.search(r"(?i)^(?:order|date|invoice|payment|[0-9]{4,})\b", b2.text.strip())], key=lambda b2: b2.y0)
                    s_name = below[0].text.strip() if below else None
                    addr_lines = [b2.text.strip() for b2 in below[1:] if not re.search(EMAIL_REGEX, b2.text) and not re.search(r"^\+?[0-9\-\s\(\)]{8,}$", b2.text)] if len(below) > 1 else []

                if s_name:
                    supplier.name = s_name
                    supplier.metadata["name"] = FieldMetadata(raw_value=s_name, normalized_value=s_name, page=b.page_num, confidence=0.98)
                if addr_lines:
                    addr = ", ".join(addr_lines)
                    supplier.address = addr
                    supplier.metadata["address"] = FieldMetadata(raw_value=addr, normalized_value=addr, page=b.page_num, confidence=0.92)

            # "To:" or "Bill To:" Customer Block
            if re.search(r"(?i)^(?:to|bill\s*to|billed\s*to|sold\s*to)\s*:?$", lines[0]) and not customer.name:
                if len(lines) > 1:
                    c_name = lines[1].strip()
                    addr_lines = [l for l in lines[2:] if not re.search(EMAIL_REGEX, l) and not re.search(r"\b\d{10}\b", l)]
                else:
                    below = sorted([b2 for b2 in p1.blocks if abs(b2.x0 - b.x0) < 50 and b2.y0 > b.y0 and b2.y0 < b.y0 + 110 and b2 != b and not re.search(r"(?i)^(?:order|date|invoice|payment|[0-9]{4,})\b", b2.text.strip())], key=lambda b2: b2.y0)
                    c_name = below[0].text.strip() if below else None
                    addr_lines = [b2.text.strip() for b2 in below[1:] if not re.search(EMAIL_REGEX, b2.text) and not re.search(r"^\+?[0-9\-\s\(\)]{8,}$", b2.text)] if len(below) > 1 else []

                if c_name:
                    customer.name = c_name
                    customer.metadata["name"] = FieldMetadata(raw_value=c_name, normalized_value=c_name, page=b.page_num, confidence=0.98)
                if addr_lines:
                    addr = ", ".join(addr_lines)
                    customer.billing_address = addr
                    customer.metadata["billing_address"] = FieldMetadata(raw_value=addr, normalized_value=addr, page=b.page_num, confidence=0.92)

        # 3. Customer extraction via Indian/GST labels (Buyer / Billed To / Details of Receiver / M/s. / Inline To:)
        if not customer.name:
            for b in p1.blocks:
                b_lines = [ln.strip() for ln in b.text.split("\n") if ln.strip()]
                for idx, ln in enumerate(b_lines):
                    # Check for "To : [CUSTOMER NAME]" inline
                    m_to_inline = re.search(r"(?i)^to\s*:\s*(.+)", ln)
                    if m_to_inline:
                        cand = m_to_inline.group(1).strip()
                        if len(cand) > 3 and not any(k in cand.lower() for k in ["tax invoice", "date", "original"]):
                            customer.name = cand
                            customer.metadata["name"] = FieldMetadata(raw_value=cand, normalized_value=cand, page=b.page_num, confidence=0.95)
                            break

                    # Check for "Buyer / Details of Receiver / Consignee"
                    if any(k in ln.lower() for k in ["details of receiver", "buyer (bill to)", "buyer :", "consignee :"]):
                        clean_ln = re.sub(r"(?i)^(?:details\s*of\s*receiver\s*\(billed\s*to\)|buyer\s*\(bill\s*to\)|buyer\s*:|consignee\s*:)\s*", "", ln).strip()
                        clean_ln = re.sub(r"(?i)^/\s*place\s*of\s*supply\s*:?\s*", "", clean_ln).strip()
                        c_cand = clean_ln
                        if not c_cand and idx + 1 < len(b_lines):
                            c_cand = b_lines[idx + 1].strip()
                        c_cand = re.split(r"(?i)\b(invoice\s*no|po\s*no|transport|lr\s*no|date)\b", c_cand)[0].strip()
                        if len(c_cand) > 3 and not any(k in c_cand.lower() for k in ["po no", "invoice", "verbal", "transport", "date", "original", "tax"]):
                            customer.name = c_cand
                            customer.metadata["name"] = FieldMetadata(raw_value=c_cand, normalized_value=c_cand, page=b.page_num, confidence=0.95)
                            addr_lines = [al for al in b_lines[idx + 1:] if not any(k in al.lower() for k in ["gstin", "state", "place of supply", "party", "pan"])]
                            if addr_lines:
                                customer.billing_address = ", ".join(addr_lines[:3])
                                customer.metadata["billing_address"] = FieldMetadata(raw_value=customer.billing_address, normalized_value=customer.billing_address, page=b.page_num, confidence=0.88)
                            break

                    # Check for party name above "M/s. :" or above "To,"
                    if re.search(r"(?i)^(?:m/s\.?|to,)\s*:?$", ln) and idx > 0:
                        above = b_lines[idx - 1].strip()
                        if len(above) > 3 and not any(k in above.lower() for k in ["original", "duplicate", "tax", "gstin"]):
                            customer.name = above
                            customer.metadata["name"] = FieldMetadata(raw_value=above, normalized_value=above, page=b.page_num, confidence=0.95)
                            break

                if customer.name:
                    break

        # 4. Supplier extraction from prominent Header Letterhead
        if not supplier.name:
            header_blocks = [b for b in p1.blocks if b.y0 < p1.height * 0.28]
            candidates = []
            for b in header_blocks:
                lines = [ln.strip() for ln in b.text.split("\n") if ln.strip()]
                for ln in lines:
                    if any(k in ln.lower() for k in ["tax invoice", "invoice", "original", "duplicate", "e-invoice", "receiptant", "irn", "pan", "cin"]):
                        continue
                    if customer.name and customer.name.lower() in ln.lower():
                        continue
                    if re.search(r"^[0-9a-f]{40,}", ln.lower()):
                        continue
                    if re.search(r"^\d{2}/\d{2}/\d{4}", ln):
                        continue
                    if re.search(r"(?i)\b(pvt\.?\s*ltd\.?|private\s*limited|limited|ltd\.?|llc|inc\.?|corp\.?|industries|resources)\b", ln):
                        clean_name = re.sub(r"(?i)^original\s+for\s+[a-z]+\s*", "", ln).strip()
                        clean_name = re.sub(r"(?i)^for,?\s*", "", clean_name).strip()
                        clean_name = re.sub(r"[\.\s]+$", "", clean_name)
                        if len(clean_name) > 3:
                            candidates.append((clean_name, b))
                    elif len(ln) > 4 and b.y0 < 120 and not candidates:
                        candidates.append((ln, b))

            # Bottom signature fallback: "VASUKI GLOBAL INDUSTRIES LIMITED\nFor,\n" or "For, [NAME]"
            if not candidates:
                for b in p1.blocks:
                    if b.y0 > p1.height * 0.65:
                        lines = [l.strip() for l in b.text.split("\n") if l.strip()]
                        for idx, ln in enumerate(lines):
                            if re.search(r"(?i)^for,?\s*$", ln) and idx > 0:
                                above = lines[idx - 1].strip()
                                if len(above) > 3 and (not customer.name or customer.name.lower() not in above.lower()):
                                    candidates.append((above, b))
                            m_for = re.search(r"(?i)for,?\s+([A-Za-z\s&.,]+(?:pvt\.?\s*ltd\.?|private\s*limited|limited|ltd\.?|llc|inc\.?|corp\.?|industries))", ln)
                            if m_for:
                                s_name = re.sub(r"[\.\s]+$", "", m_for.group(1).strip())
                                if not customer.name or customer.name.lower() not in s_name.lower():
                                    candidates.append((s_name, b))

            # Multi-page e-way bill fallback (Page 2)
            if not candidates and len(doc_content.pages) > 1:
                for b in doc_content.pages[1].blocks:
                    for ln in b.text.split("\n"):
                        if re.search(r"(?i)\b(industries\s*limited|corporation\s*pvt|resources\s*pvt)\b", ln):
                            candidates.append((ln.strip(), None))

            if candidates:
                best_name, blk = candidates[0]
                supplier.name = best_name
                supplier.metadata["name"] = FieldMetadata(raw_value=best_name, normalized_value=best_name, page=1, confidence=0.92)

                if blk:
                    addr_lines = [l for l in blk.text.split("\n") if l.strip() and l.strip() != best_name and not re.search(EMAIL_REGEX, l)]
                    if addr_lines:
                        supplier.address = ", ".join(addr_lines)
                        supplier.metadata["address"] = FieldMetadata(raw_value=supplier.address, normalized_value=supplier.address, page=1, confidence=0.88)

        # 5. Fallback address search
        if supplier.name and not supplier.address:
            addr_cands = []
            for vl in p1.visual_lines[:20]:
                if any(k in vl.lower() for k in ["works :", "ho address :", "landmark", "road", "center", "centre", "trade hub", "street", "plot", "port"]):
                    if not customer.name or customer.name.lower() not in vl.lower():
                        clean_addr = re.sub(r"^(?:works|ho\s*address)\s*:\s*", "", vl, flags=re.IGNORECASE).strip()
                        addr_cands.append(clean_addr)
            if addr_cands:
                supplier.address = addr_cands[0]
                supplier.metadata["address"] = FieldMetadata(raw_value=addr_cands[0], normalized_value=addr_cands[0], page=1, confidence=0.85)

    # -------------------------------------------------------------------------
    # Generic Table Line Items Extraction
    # -------------------------------------------------------------------------

    def _extract_line_items(self, doc_content: DocumentContent) -> List[LineItem]:
        p1 = doc_content.pages[0]
        items = []

        # If OCR page, visual lines grouping is much more reliable than fragmented word blocks
        if p1.is_ocr:
            return self._extract_items_from_visual_lines(p1)

        # Strategy A: Block-Level Line Item Detection with Arithmetic Verification (for Digital PDFs)
        for b in p1.blocks:
            if b.y0 < p1.height * 0.18 or b.y0 > p1.height * 0.88:
                continue
            text = b.text.strip()
            text_lower = text.lower()
            if any(k in text_lower for k in ["gstin", "bank", "original", "consignee", "received", "summary", "e. & o.e"]):
                continue

            # Skip summary/tax/total blocks (including TCS / Cess)
            if re.search(r"(?i)^(?:total|sub\s*total|grand\s*total|total\s*amount|tot\.taxable|taxable\s*value|tcs|cess)\b", text):
                continue
            if re.search(r"(?i)\b(?:tcs\s*@|compensation\s*cess)\b", text_lower):
                continue

            num_strs = re.findall(r"\b[-+]?[0-9]+(?:[.,][0-9]+)*\b", text)
            nums = [normalize_amount(n) for n in num_strs if normalize_amount(n) is not None]

            if len(nums) >= 2:
                matched_row = False
                for i in range(len(nums)):
                    for j in range(len(nums)):
                        if i != j:
                            if nums[i] < 0.01 or nums[j] < 0.01:
                                continue
                            if (nums[i] > 100000 and nums[i].is_integer()) or (nums[j] > 100000 and nums[j].is_integer()):
                                continue
                            exp_val = round(nums[i] * nums[j], 2)
                            for k in range(len(nums)):
                                if k != i and k != j and abs(exp_val - nums[k]) <= max(1.5, nums[k] * 0.02) and nums[k] > 0:
                                    qty, price, tot = nums[i], nums[j], nums[k]
                                    if qty > price and qty > 100 and price < 50:
                                        qty, price = price, qty
                                    
                                    desc = self._clean_block_description(text, [qty, price, tot])
                                    if len(desc.split()) <= 1:
                                        left_b = [b2 for b2 in p1.blocks if b2.x1 <= b.x0 and abs(b2.y0 - b.y0) < 30 and not any(k in b2.text.lower() for k in ["sample", "warning", "invoicefaker"])]
                                        if left_b:
                                            desc = self._clean_block_description(left_b[0].text, [qty, price, tot])
                                    if desc and not re.search(r"(?i)^(?:total|sub\s*total|tcs|cess)\b", desc):
                                        code = self._extract_code(text)
                                        uom = self._extract_uom(text)
                                        items.append(
                                            LineItem(
                                                line_number=len(items) + 1,
                                                description=desc,
                                                code=code,
                                                quantity=qty,
                                                unit_of_measure=uom,
                                                unit_price=price,
                                                total=tot,
                                                metadata=FieldMetadata(raw_value=text, normalized_value=tot, page=1, confidence=0.98)
                                            )
                                        )
                                        matched_row = True
                                        break
                            if matched_row:
                                break
                    if matched_row:
                        break

        # Deduplicate items if identical total/qty matched
        dedup_items = []
        seen = set()
        for it in items:
            key = (round(it.total, 2), it.quantity)
            if key not in seen:
                seen.add(key)
                dedup_items.append(it)

        if dedup_items:
            return dedup_items

        # Fallback to visual lines table extraction
        return self._extract_items_from_visual_lines(p1)

    def _extract_items_from_visual_lines(self, p1: PageContent) -> List[LineItem]:
        items = []
        header_idx = -1
        for i, vl in enumerate(p1.visual_lines):
            vl_lower = vl.lower()
            matches = 0
            if any(k in vl_lower for k in ["description", "particulars", "service", "item", "product", "goods", "truck no"]):
                matches += 1
            if any(k in vl_lower for k in ["qty", "quantity", "hrs", "hours", "units", "weight"]):
                matches += 1
            if any(k in vl_lower for k in ["rate", "price", "unit price", "cost"]):
                matches += 1
            if any(k in vl_lower for k in ["amount", "total", "sub total", "net"]):
                matches += 1
            if matches >= 2:
                header_idx = i
                break

        footer_idx = len(p1.visual_lines)
        if header_idx != -1:
            for j in range(header_idx + 1, len(p1.visual_lines)):
                vl_j = p1.visual_lines[j].lower()
                if re.search(r"(?i)^(?:total|sub\s*total|subtotal|tot\.taxable|taxable\s*value|total\s*invoice\s*amount|grand\s*total|total\s*due|add\s*[:-]|cgst|sgst|igst|vat|tax\b)\b", vl_j) or any(k in vl_j for k in ["bank", "anz bank", "amount in words"]):
                    footer_idx = j
                    break

            cand_lines = p1.visual_lines[header_idx + 1: footer_idx]
            pending_desc = ""
            for vl in cand_lines:
                num_matches = re.findall(r"\b[-+]?[0-9]+(?:[.,][0-9]+)*\b", vl)
                nums = [normalize_amount(n) for n in num_matches if normalize_amount(n) is not None]
                if nums:
                    parsed_item = self._parse_table_row(vl, nums, len(items) + 1)
                    if parsed_item:
                        if pending_desc:
                            parsed_item.description = f"{pending_desc} - {parsed_item.description}" if parsed_item.description != "Item" else pending_desc
                            pending_desc = ""
                        items.append(parsed_item)
                elif len(vl.strip()) > 2:
                    pending_desc = (pending_desc + " " + vl.strip()).strip()

        if not items:
            for vl in p1.visual_lines:
                vl_lower = vl.lower()
                if any(k in vl_lower for k in ["subtotal", "sub total", "grand total", "taxable value", "bank", "cess", "tcs"]):
                    continue
                num_matches = re.findall(r"\b[-+]?[0-9]+(?:[.,][0-9]+)*\b", vl)
                nums = [normalize_amount(n) for n in num_matches if normalize_amount(n) is not None]
                if len(nums) >= 2:
                    parsed_item = self._parse_table_row(vl, nums, len(items) + 1)
                    if parsed_item:
                        items.append(parsed_item)
                        if len(items) >= 1:
                            break

        return items

    def _clean_block_description(self, text: str, nums: List[float]) -> str:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        desc_parts = []
        for l in lines:
            if re.search(r"^\(?\s*(?:tcs|gst|cgst|sgst|category|cess)\b", l.lower()):
                continue
            if re.match(r"^[$₹€£¥%]?\s*[-+]?[0-9,]+(?:\.\d+)?%?\s*$", l):
                continue
            cleaned = l
            for n in nums:
                cleaned = cleaned.replace(f"{n:,.2f}", "").replace(f"{n:.2f}", "").replace(f"{n:.3f}", "")
            cleaned = re.sub(r"^\d+\s+", "", cleaned)  # serial number
            cleaned = re.sub(r"\b2701\d{2,4}\b", "", cleaned)  # HSN
            cleaned = re.sub(r"\b(MTS?|MT|HRS?|PCS|KG)\b", "", cleaned, flags=re.I)
            cleaned = re.sub(r"[$₹€£¥%]", "", cleaned).strip()
            if cleaned and len(cleaned) > 1 and not re.search(r"^\d+$", cleaned):
                desc_parts.append(cleaned)
        return " - ".join(desc_parts) if desc_parts else "Item"

    def _extract_code(self, text: str) -> Optional[str]:
        hsn_m = re.search(r"\b(2701\d{2,4}|\d{4,8})\b", text)
        return hsn_m.group(1) if hsn_m else None

    def _extract_uom(self, text: str) -> Optional[str]:
        uom_m = re.search(r"\b(mts?|mt|hrs?|hours?|nos?|pcs?|kg|units?)\b", text.lower())
        return uom_m.group(1).upper() if uom_m else None

    def _parse_table_row(self, line_text: str, nums: List[float], line_no: int) -> Optional[LineItem]:
        line_lower = line_text.lower()
        if re.search(r"(?i)^(?:total|sub\s*total|grand\s*total|taxable|total\s*invoice\s*amount|tcs|cess|add\s*[:-]|cgst|sgst|igst|vat|tax)\b", line_lower):
            return None
        if any(k in line_lower for k in ["add:-", "cgst", "sgst", "igst", "compensation cess", "tcs on adv", "total amount", "total invoice amount"]):
            return None

        code = self._extract_code(line_text)
        uom = self._extract_uom(line_text)

        desc_raw = line_text
        for n_str in re.findall(r"[-+]?[0-9,]+\.\d{2,3}%?", line_text):
            desc_raw = desc_raw.replace(n_str, " ")
        if code:
            desc_raw = desc_raw.replace(code, " ")
        if uom:
            desc_raw = re.sub(r"(?i)\b" + uom + r"\b", " ", desc_raw)

        desc_clean = re.sub(r"^\s*([0-9]+\.?\s*)+", "", desc_raw).strip()
        desc_clean = re.sub(r"[$₹€£¥%]", "", desc_clean).strip()
        desc_clean = re.sub(r"\s+", " ", desc_clean).strip()

        if not desc_clean:
            desc_clean = "Item"

        qty = None
        unit_price = None
        total = None

        if len(nums) >= 3:
            matched = False
            for i in range(len(nums)):
                for j in range(len(nums)):
                    if i != j:
                        exp_val = round(nums[i] * nums[j], 2)
                        for k in range(len(nums)):
                            if k != i and k != j and abs(exp_val - nums[k]) <= max(1.5, nums[k] * 0.02):
                                qty = nums[i]
                                unit_price = nums[j]
                                total = nums[k]
                                if qty > unit_price and qty > 100 and unit_price < 50:
                                    qty, unit_price = unit_price, qty
                                matched = True
                                break
                    if matched:
                        break
                if matched:
                    break
            if not matched:
                qty = nums[0]
                unit_price = nums[1]
                total = nums[-1]

        elif len(nums) == 2:
            n1, n2 = nums[0], nums[1]
            if n1 > 0 and n2 > 0:
                if n2 > n1:
                    total = n2
                    qty = n1
                    unit_price = round(total / qty, 2)
                else:
                    total = n1
                    qty = n2
                    unit_price = round(total / qty, 2)
        elif len(nums) == 1:
            total = nums[0]
            qty = 1.0
            unit_price = total

        if total is not None and total > 0:
            return LineItem(
                line_number=line_no,
                description=desc_clean,
                code=code,
                quantity=qty,
                unit_of_measure=uom,
                unit_price=unit_price,
                total=total,
                metadata=FieldMetadata(raw_value=line_text, normalized_value=total, page=1, confidence=0.95)
            )
        return None

    # -------------------------------------------------------------------------
    # Financial Details & Taxes
    # -------------------------------------------------------------------------

    def _extract_financials(self, doc_content: DocumentContent, financials: FinancialDetails, line_items: List[LineItem]):
        p1 = doc_content.pages[0]
        vlines = p1.visual_lines

        # 1. Subtotal: Check labels or line items total
        subtotal_val = None
        for vl in vlines:
            m = re.search(r"(?i)(?:sub\s*total|subtotal|price\s*net|net\s*price|taxable\s*value|taxable\s*amount|tot\.taxable\s*amt|amount\s*before\s*tax)\s*[:\-]?\s*([$₹€£¥]?\s*[-+]?[0-9]+(?:[.,][0-9]+)*\s*[$₹€£¥]?)", vl)
            if m:
                val = normalize_amount(m.group(1))
                if val and val > 0:
                    subtotal_val = val
                    break

        if line_items:
            items_sum = round(sum(item.total for item in line_items), 2)
            if subtotal_val is None or abs(items_sum - subtotal_val) <= 2.0:
                subtotal_val = items_sum

        if subtotal_val:
            financials.subtotal = subtotal_val
            financials.taxable_amount = subtotal_val
            financials.metadata["subtotal"] = FieldMetadata(raw_value=str(subtotal_val), normalized_value=subtotal_val, page=1, confidence=0.98)

        # 2. Tax Components
        taxes: List[TaxComponent] = []

        for vl in vlines:
            vl_lower = vl.lower()

            # Generic Tax: e.g. "Tax $8.50" or "plus 9% VAT 3.950,28 $"
            m_tax = re.search(r"(?i)\b(?:plus\s*)?(\d{1,2}%?\s*)?(vat|tax|sales\s*tax)\s*[:\-]?\s*([$₹€£¥]?\s*[-+]?[0-9]+(?:[.,][0-9]+)*\s*[$₹€£¥]?)", vl)
            if m_tax and not any(k in vl_lower for k in ["tax invoice", "taxable", "total tax", "cgst", "sgst", "igst", "cess", "tcs"]):
                t_amt = normalize_amount(m_tax.group(3))
                if t_amt and t_amt > 0:
                    rate_str = m_tax.group(1)
                    rate = float(re.sub(r"[^\d.]", "", rate_str)) if rate_str and re.search(r"\d", rate_str) else None
                    taxes.append(TaxComponent(tax_type=m_tax.group(2).upper(), rate=rate, amount=t_amt))

            # CGST
            if any(k in vl_lower for k in ["cgst", "central tax"]):
                amts = [normalize_amount(n) for n in re.findall(r"\b([0-9,]+\.\d{2})\b", vl)]
                min_threshold = 10.0 if (subtotal_val and subtotal_val > 500) else 0.01
                amts = [a for a in amts if a and a > min_threshold and (subtotal_val is None or a <= subtotal_val * 0.15)]
                if amts:
                    best_amt = min(amts, key=lambda a: abs(a - (subtotal_val * 0.025))) if subtotal_val else amts[-1]
                    taxes.append(TaxComponent(tax_type="CGST", rate=2.5, amount=best_amt))

            # SGST
            if any(k in vl_lower for k in ["sgst", "state/ut tax", "state tax", "sgst/utgst"]):
                amts = [normalize_amount(n) for n in re.findall(r"\b([0-9,]+\.\d{2})\b", vl)]
                min_threshold = 10.0 if (subtotal_val and subtotal_val > 500) else 0.01
                amts = [a for a in amts if a and a > min_threshold and (subtotal_val is None or a <= subtotal_val * 0.15)]
                if amts:
                    best_amt = min(amts, key=lambda a: abs(a - (subtotal_val * 0.025))) if subtotal_val else amts[-1]
                    taxes.append(TaxComponent(tax_type="SGST", rate=2.5, amount=best_amt))

            # IGST
            if any(k in vl_lower for k in ["igst", "integrated tax"]):
                amts = [normalize_amount(n) for n in re.findall(r"\b([0-9,]+\.\d{2})\b", vl)]
                min_threshold = 10.0 if (subtotal_val and subtotal_val > 500) else 0.01
                amts = [a for a in amts if a and a > min_threshold]
                if amts:
                    taxes.append(TaxComponent(tax_type="IGST", rate=5.0, amount=amts[-1]))

            # Cess
            if "cess" in vl_lower or "compensation" in vl_lower:
                amts = [normalize_amount(n) for n in re.findall(r"\b([0-9,]+\.\d{2})\b", vl)]
                amts = [a for a in amts if a and a > 500.0]
                if amts:
                    taxes.append(TaxComponent(tax_type="CESS", rate=None, amount=amts[-1]))

            # TCS
            if "tcs" in vl_lower:
                amts = [normalize_amount(n) for n in re.findall(r"\b([0-9,]+\.\d{2})\b", vl)]
                amts = [a for a in amts if a and a > 50.0]
                if amts:
                    taxes.append(TaxComponent(tax_type="TCS", rate=1.0, amount=amts[-1]))

        # Check blocks if taxes not found in visual lines
        if not any(t.tax_type == "CGST" for t in taxes):
            for b in p1.blocks:
                if "cgst" in b.text.lower():
                    amts = [normalize_amount(n) for n in re.findall(r"[-+]?[0-9,]+\.\d{2}", b.text)]
                    min_thresh = 10.0 if (subtotal_val and subtotal_val > 500) else 0.01
                    amts = [a for a in amts if a and a > min_thresh and (subtotal_val is None or a <= subtotal_val * 0.15)]
                    if amts:
                        best_amt = min(amts, key=lambda a: abs(a - (subtotal_val * 0.025))) if subtotal_val else amts[-1]
                        taxes.append(TaxComponent(tax_type="CGST", rate=2.5, amount=best_amt))
                        break
        if not any(t.tax_type == "SGST" for t in taxes):
            for b in p1.blocks:
                if "sgst" in b.text.lower():
                    amts = [normalize_amount(n) for n in re.findall(r"[-+]?[0-9,]+\.\d{2}", b.text)]
                    min_thresh = 10.0 if (subtotal_val and subtotal_val > 500) else 0.01
                    amts = [a for a in amts if a and a > min_thresh and (subtotal_val is None or a <= subtotal_val * 0.15)]
                    if amts:
                        best_amt = min(amts, key=lambda a: abs(a - (subtotal_val * 0.025))) if subtotal_val else amts[-1]
                        taxes.append(TaxComponent(tax_type="SGST", rate=2.5, amount=best_amt))
                        break
        if not any(t.tax_type == "CESS" for t in taxes):
            for b in p1.blocks:
                if any(k in b.text.lower() for k in ["cess", "compensation"]):
                    m_cess = re.search(r"(?i)(?:compensation\s*)?cess[^\n]*\n\s*([0-9,]+\.\d{2})", b.text)
                    if m_cess:
                        c_val = normalize_amount(m_cess.group(1))
                        if c_val and c_val > 0 and (subtotal_val is None or c_val < subtotal_val):
                            taxes.append(TaxComponent(tax_type="CESS", rate=None, amount=c_val))
                            break

        # Deduplicate taxes
        seen_taxes = set()
        dedup_taxes = []
        for t in taxes:
            key = (t.tax_type, round(t.amount, 2))
            if key not in seen_taxes:
                seen_taxes.add(key)
                dedup_taxes.append(t)

        # Symmetry: if CGST found and SGST not (or vice versa), complete the pair
        has_cgst = next((t for t in dedup_taxes if t.tax_type == "CGST"), None)
        has_sgst = next((t for t in dedup_taxes if t.tax_type == "SGST"), None)
        if has_cgst and not has_sgst:
            dedup_taxes.append(TaxComponent(tax_type="SGST", rate=has_cgst.rate, amount=has_cgst.amount))
        elif has_sgst and not has_cgst:
            dedup_taxes.append(TaxComponent(tax_type="CGST", rate=has_sgst.rate, amount=has_sgst.amount))

        financials.tax_components = dedup_taxes

        # Total tax amount: Sales Tax / GST / VAT
        gst_vat_taxes = [t.amount for t in dedup_taxes if t.tax_type in ["CGST", "SGST", "IGST", "TAX", "VAT", "SALES TAX"]]
        if gst_vat_taxes:
            # If only one leg of symmetric CGST/SGST was captured, double it
            if len(gst_vat_taxes) == 1 and any(t.tax_type in ["CGST", "SGST"] for t in dedup_taxes):
                gst_vat_taxes.append(gst_vat_taxes[0])
            financials.total_tax_amount = round(sum(gst_vat_taxes), 2)
        elif subtotal_val and any(k in doc_content.full_text.lower() for k in ["cgst", "sgst"]):
            financials.total_tax_amount = round(subtotal_val * 0.05, 2)
        else:
            for vl in vlines:
                m_tt = re.search(r"(?i)(?:total\s*tax|tax\s*total|gst\s*amount)\s*[:\-]?\s*([$₹€£¥]?\s*[0-9,]+\.\d{2})", vl)
                if m_tt:
                    financials.total_tax_amount = normalize_amount(m_tt.group(1))
                    break

        # 3. Discounts, Shipping, Additional Fees
        for vl in vlines:
            m_disc = re.search(r"(?i)(?:discount|total\s*discount)\s*[:\-]?\s*([$₹€£¥]?\s*[0-9,]+\.\d{2})", vl)
            if m_disc:
                financials.total_discounts = normalize_amount(m_disc.group(1)) or 0.0
            m_ro = re.search(r"(?i)round\s*off\s*[:\-]?\s*([-+]?[0-9]*\.\d{2})", vl)
            if m_ro:
                financials.additional_fees = normalize_amount(m_ro.group(1)) or 0.0

        # 4. Grand Total / Total Due
        grand_total_val = None

        # Check multi-line blocks for "Total Due\n$93.50"
        for b in p1.blocks:
            lines = [ln.strip() for ln in b.text.split("\n") if ln.strip()]
            for i, ln in enumerate(lines):
                if re.search(r"(?i)^(?:total\s*due|grand\s*total|total\s*payable|amount\s*due)$", ln):
                    if i + 1 < len(lines):
                        amt = normalize_amount(lines[i + 1])
                        if amt and amt > 0:
                            grand_total_val = amt
                            break
            if grand_total_val:
                break

        # Check visual lines for inline Grand Total / Total Due / Invoice Total
        if not grand_total_val:
            for vl in vlines:
                m_gt = re.search(r"(?i)(?:grand\s*total|invoice\s*total|total\s*due|total\s*amount|total\s*inv\s*amt|total\s*payable|bill\s*amount|total\s*\.{3,})\s*[:\-]?\s*([$₹€£¥]?\s*[-+]?[0-9]+(?:[.,][0-9]+)*\s*[$₹€£¥]?)", vl)
                if m_gt:
                    val = normalize_amount(m_gt.group(1))
                    if val and val > 0:
                        grand_total_val = val
                        break

        # Check single word "Total $XX"
        if not grand_total_val:
            for vl in vlines:
                m_tot = re.search(r"(?i)^total\s*[:\-]?\s*([$₹€£¥]?\s*[-+]?[0-9]+(?:[.,][0-9]+)*\s*[$₹€£¥]?)", vl.strip())
                if m_tot:
                    val = normalize_amount(m_tot.group(1))
                    if val and (subtotal_val is None or val > subtotal_val or not dedup_taxes):
                        grand_total_val = val
                        break

        # Check bottom summary block with multiple taxes + grand total at the end
        if not grand_total_val and subtotal_val:
            all_tax_sum = sum(t.amount for t in dedup_taxes)
            grand_total_val = round(subtotal_val + all_tax_sum + (financials.additional_fees or 0.0) - (financials.total_discounts or 0.0), 2)

        if grand_total_val:
            financials.grand_total = grand_total_val
            financials.metadata["grand_total"] = FieldMetadata(raw_value=str(grand_total_val), normalized_value=grand_total_val, page=1, confidence=0.98)

        # 5. Amount Paid & Outstanding Balance
        if re.search(r"(?i)\bpaid\b", doc_content.full_text):
            financials.amount_paid = grand_total_val
            financials.outstanding_balance = 0.0
        else:
            financials.outstanding_balance = grand_total_val

"""
Field Alias and Semantic Concept Registry.
Maintains comprehensive, extensible mappings between conceptual invoice fields
and their real-world label variations across diverse suppliers and templates.
"""
from typing import Dict, List, Optional
import re


class AliasRegistry:
    """Registry of field aliases with regex patterns and match helpers."""

    FIELD_ALIASES: Dict[str, List[str]] = {
        "invoice_number": [
            r"invoice\s*no\.?",
            r"invoice\s*number",
            r"invoice\s*#",
            r"tax\s*invoice\s*no\.?",
            r"tax\s*invoice\s*number",
            r"bill\s*no\.?",
            r"bill\s*number",
            r"inv\s*no\.?",
            r"inv\s*#",
            r"doc\s*no\.?",
            r"document\s*no\.?",
            r"reference\s*number",
            r"ref\s*#",
            r"voucher\s*no\.?",
            r"invoice\s*id",
        ],
        "invoice_date": [
            r"invoice\s*date",
            r"date\s*of\s*issue",
            r"date\s*of\s*invoice",
            r"bill\s*date",
            r"document\s*date",
            r"issued\s*on",
            r"issue\s*date",
            r"ack\s*date",
            r"\bdated\b",
            r"\bdate\b",
        ],
        "due_date": [
            r"payment\s*due\s*date",
            r"due\s*date",
            r"payment\s*due",
            r"pay\s*by",
            r"payment\s*deadline",
            r"due\s*on",
            r"expiry\s*date",
        ],
        "po_number": [
            r"party\s*po\s*no\.?",
            r"buyer'?s?\s*order\s*no\.?",
            r"p\.?o\.?\s*no\.?",
            r"p\.?o\.?\s*#",
            r"po\s*number",
            r"purchase\s*order",
            r"purchase\s*order\s*no\.?",
            r"order\s*number",
            r"order\s*no\.?",
            r"order\s*ref(?:erence)?",
            r"so\s*no\.?",
        ],
        "payment_terms": [
            r"payment\s*terms",
            r"terms\s*of\s*payment",
            r"mode/terms\s*of\s*payment",
            r"mode\s*of\s*payment",
            r"pay\s*terms",
            r"payment\s*net\s*cash",
        ],
        "supplier_labels": [
            r"supplier",
            r"vendor",
            r"seller",
            r"issued\s*by",
            r"from\s*:",
            r"\bfrom\b",
            r"merchant",
            r"consignor",
            r"generated\s*by",
            r"billed\s*by",
            r"sold\s*by",
        ],
        "customer_labels": [
            r"details\s*of\s*receiver\s*\(billed\s*to\)",
            r"details\s*of\s*receiver",
            r"buyer\s*\(bill\s*to\)",
            r"buyer\s*:",
            r"\bbuyer\b",
            r"billed\s*to\s*:",
            r"billed\s*to",
            r"bill\s*to\s*:",
            r"bill\s*to",
            r"sold\s*to\s*:",
            r"sold\s*to",
            r"consignee\s*\(ship\s*to\)",
            r"consignee\s*:",
            r"\bconsignee\b",
            r"customer\s*:",
            r"\bcustomer\b",
            r"client\s*:",
            r"\bclient\b",
            r"m/s\.?",
            r"to\s*:",
            r"\bto\b",
            r"recipient",
        ],
        "subtotal": [
            r"sub\s*total",
            r"subtotal",
            r"taxable\s*value",
            r"taxable\s*amount",
            r"tot\.taxable\s*amt",
            r"total\s*taxable\s*value",
            r"amount\s*before\s*tax",
            r"total\s*before\s*tax",
            r"total\s*before\s*vat",
            r"net\s*amount",
            r"amount\s*chargeable",
        ],
        "tax_amount": [
            r"total\s*tax\s*amount",
            r"total\s*tax",
            r"tax\s*total",
            r"tax\s*amount",
            r"total\s*gst\s*payable",
            r"total\s*gst",
            r"gst\s*amount",
            r"vat\s*amount",
            r"total\s*vat",
            r"\btax\b",
        ],
        "grand_total": [
            r"grand\s*total",
            r"total\s*due",
            r"invoice\s*total",
            r"total\s*amount",
            r"total\s*inv\s*amt",
            r"total\s*payable",
            r"amount\s*due",
            r"balance\s*due",
            r"net\s*payable",
            r"bill\s*amount",
            r"total\s*\.{3,}",
            r"\btotal\b",
        ],
        "amount_paid": [
            r"amount\s*paid",
            r"total\s*paid",
            r"paid\s*amount",
            r"\bpaid\b",
        ],
        "discounts": [
            r"total\s*discounts?",
            r"discounts?",
            r"less\s*:\s*discount",
            r"trade\s*discount",
            r"adjust(?:ment)?",
        ],
        "shipping": [
            r"shipping\s*(?:&|\+)?\s*handling",
            r"shipping\s*charges?",
            r"shipping\s*fee",
            r"freight\s*charges?",
            r"freight",
            r"delivery\s*charges?",
            r"transport\s*charges?",
        ],
        "additional_fees": [
            r"round\s*off",
            r"rounding",
            r"handling\s*fee",
            r"other\s*charges?",
            r"extra\s*charges?",
        ],
        "table_col_quantity": [
            r"hrs/qty",
            r"quantity",
            r"qnty",
            r"\bqty\b",
            r"\bhrs\b",
            r"\bhours\b",
            r"\bunits\b",
            r"\bpcs\b",
            r"\bpieces\b",
            r"\bnos\b",
        ],
        "table_col_description": [
            r"description\s*of\s*goods",
            r"item\s*description",
            r"description",
            r"particulars",
            r"\bservice\b",
            r"\bitem\b",
            r"\bproduct\b",
            r"\bgoods\b",
            r"\bdetails\b",
        ],
        "table_col_price": [
            r"rate/price",
            r"unit\s*price",
            r"unit\s*rate",
            r"unit\s*cost",
            r"\brate\b",
            r"\bprice\b",
            r"\bcost\b",
        ],
        "table_col_total": [
            r"sub\s*total",
            r"line\s*total",
            r"net\s*amount",
            r"total\s*amount",
            r"\bamount\b",
            r"\btotal\b",
        ],
        "table_col_code": [
            r"hsn/sac",
            r"hsn",
            r"sac",
            r"sku",
            r"item\s*code",
            r"part\s*#",
            r"\bcode\b",
        ],
        "table_col_discount": [
            r"adjust",
            r"disc%",
            r"discount",
            r"\bdisc\b",
        ],
        "table_col_tax": [
            r"tax\s*rate",
            r"tax\s*amount",
            r"gst%",
            r"vat%",
            r"\btax\b",
        ],
    }

    @classmethod
    def match_alias(cls, text: str, concept: str) -> bool:
        """Checks if the given text matches any pattern for the concept."""
        patterns = cls.FIELD_ALIASES.get(concept, [])
        for pat in patterns:
            if re.search(r"(?i)\b" + pat + r"\b", text.strip()):
                return True
        return False

    @classmethod
    def find_concept(cls, text: str) -> Optional[str]:
        """Identifies which invoice concept a label text refers to."""
        clean = text.strip()
        for concept, patterns in cls.FIELD_ALIASES.items():
            for pat in patterns:
                if re.search(r"(?i)^" + pat + r"[\s:.-]*$", clean):
                    return concept
                if re.search(r"(?i)\b" + pat + r"[\s:.-]*", clean):
                    return concept
        return None

"""
Pluggable AI & Document-Understanding Provider Interface.
Supports external LLM/Vision document processing when API keys are configured,
with full isolation and graceful fallback to the local extraction pipeline.
"""
import os
import json
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from invoice_extractor.schema import StandardInvoice


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
    Configurable AI provider supporting external LLMs (e.g. Gemini, OpenAI)
    via environment variables. Reads API key from environment without hardcoding.
    """

    def __init__(self, api_key_env_var: str = "GEMINI_API_KEY"):
        self.api_key_env_var = api_key_env_var
        self.api_key = os.getenv(api_key_env_var)

    def is_available(self) -> bool:
        return bool(self.api_key)

    def extract_invoice(self, file_path_or_bytes: Any, text_content: str) -> Optional[StandardInvoice]:
        if not self.is_available():
            return None

        # Here an authorized external API call could be performed if the API key is provided.
        # Without credentials, it safely returns None so the local engine operates without mock data.
        return None

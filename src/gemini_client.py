"""Gemini API client returning Pydantic-validated structured JSON."""

import os
from typing import Callable

from google import genai
from google.genai import types
from pydantic import ValidationError

from src.models import EnquiryAnalysis

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_TIMEOUT_MS = 20_000  # the SDK default is no timeout; a hung call must fail safe

SYSTEM_PROMPT = """You are an enquiry parser for a small fitness studio in Hermanus.
Your ONLY job is to classify the customer's message and extract what they are asking about as strict JSON.

You must NEVER invent prices, discounts, or availability.

Return a JSON object with exactly these keys:
- "intent": one of "package_pricing", "saturday_availability", "pricing_and_availability", "out_of_scope", "unknown"
- "details": {
    "requested_packages": list of package names asked about.
      - Use "Single session", "5 sessions", or "12 sessions" when the customer asks for those.
      - Use "all_packages" when they ask generally about packages or prices.
      - If they ask about another package not listed above (e.g., "20 sessions", "monthly membership"), include their exact phrase here.
    "requested_slots": list of time slots asked about.
      - Use "Saturday 09:00", "Saturday 10:00", or "Saturday 11:00" when the customer asks for those specific slots.
      - Use "all_saturday" when they ask generally about Saturday availability or Saturday slots.
      - If they ask about another day or time (e.g., "Sunday 10:00", "Saturday 14:00", "Monday"), include their exact phrase here.
    "unanswerable_topics": list of any questions or requests outside package pricing and Saturday availability
      (for example: booking a session, making a payment, discounts, address, nutrition, personal trainer bio).
  }
- "requires_human_approval": true (always true)
"""


class AIServiceError(Exception):
    """Raised when the Gemini API cannot be reached, times out, or fails."""


class InvalidAIOutputError(Exception):
    """Raised when the AI returns broken JSON or JSON failing Pydantic validation."""


class GeminiEnquiryParser:
    """Parses customer enquiries via Gemini and validates output with Pydantic."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        raw_call_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self._raw_call_fn = raw_call_fn

    def _call_gemini(self, customer_message: str) -> str:
        """Invoke the live Gemini API and return raw JSON response text."""
        if not self.api_key:
            raise AIServiceError(
                "GEMINI_API_KEY is not set. Add it to your local .env file."
            )

        try:
            client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
            )
            response = client.models.generate_content(
                model=self.model,
                contents=customer_message,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=EnquiryAnalysis,
                    temperature=0.0,
                ),
            )
        except Exception as exc:
            raise AIServiceError(f"Gemini API call failed: {exc}") from exc

        if not response or not getattr(response, "text", None):
            raise InvalidAIOutputError("Gemini API returned an empty response.")

        return response.text

    def parse_enquiry(self, customer_message: str) -> EnquiryAnalysis:
        """Send message to Gemini (or injected callable) and validate with Pydantic."""
        if self._raw_call_fn is not None:
            try:
                raw_text = self._raw_call_fn(customer_message)
            except (AIServiceError, InvalidAIOutputError):
                raise
            except Exception as exc:
                raise AIServiceError(f"AI service failure: {exc}") from exc
        else:
            raw_text = self._call_gemini(customer_message)

        if not isinstance(raw_text, str) or not raw_text.strip():
            raise InvalidAIOutputError("AI returned empty or non-string output.")

        try:
            return EnquiryAnalysis.model_validate_json(raw_text)
        except ValidationError as exc:
            raise InvalidAIOutputError(
                f"AI JSON failed Pydantic validation: {exc}"
            ) from exc

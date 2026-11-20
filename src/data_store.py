"""Approved local studio data loader and deterministic fact lookup."""

import json
import re
from pathlib import Path

from pydantic import ValidationError

from src.models import EnquiryAnalysis, LookupResult, StudioData


class DataUnavailableError(Exception):
    """Raised when the approved data file is missing, unreadable, or invalid."""


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation noise, and collapse whitespace."""
    cleaned = text.strip().lower()
    cleaned = cleaned.replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


# Controlled alias maps that point ONLY to exact approved keys in studio_data.json
PACKAGE_ALIASES: dict[str, str] = {
    "single session": "Single session",
    "single": "Single session",
    "1 session": "Single session",
    "one session": "Single session",
    "single pack": "Single session",
    "5 sessions": "5 sessions",
    "5 session": "5 sessions",
    "5 session pack": "5 sessions",
    "5 pack": "5 sessions",
    "five sessions": "5 sessions",
    "12 sessions": "12 sessions",
    "12 session": "12 sessions",
    "12 session pack": "12 sessions",
    "12 pack": "12 sessions",
    "twelve sessions": "12 sessions",
}

ALL_PACKAGES_TOKENS = {
    "all_packages",
    "all packages",
    "all",
    "packages",
    "package options",
    "pricing",
    "prices",
    "price list",
}

# Every alias names Saturday explicitly. A bare time such as "09:00" is NOT mapped, so a
# question about another day at the same time escalates instead of quoting Saturday's status.
SLOT_ALIASES: dict[str, str] = {
    "saturday 09:00": "Saturday 09:00",
    "saturday 9:00": "Saturday 09:00",
    "saturday 9am": "Saturday 09:00",
    "saturday 9 am": "Saturday 09:00",
    "sat 09:00": "Saturday 09:00",
    "sat 9:00": "Saturday 09:00",
    "saturday 10:00": "Saturday 10:00",
    "saturday 10am": "Saturday 10:00",
    "saturday 10 am": "Saturday 10:00",
    "sat 10:00": "Saturday 10:00",
    "saturday 11:00": "Saturday 11:00",
    "saturday 11am": "Saturday 11:00",
    "saturday 11 am": "Saturday 11:00",
    "sat 11:00": "Saturday 11:00",
}

ALL_SATURDAY_TOKENS = {
    "all_saturday",
    "all saturday",
    "all",
    "saturday",
    "saturday slots",
    "saturday availability",
    "sat",
}


class StudioDataStore:
    """Loads, validates and queries approved studio facts from a local JSON file."""

    def __init__(self, data_path: Path) -> None:
        self.data_path = Path(data_path)
        try:
            raw = json.loads(self.data_path.read_text(encoding="utf-8"))
            data = StudioData.model_validate(raw)
        except FileNotFoundError as exc:
            raise DataUnavailableError(f"Approved data file not found: {self.data_path}") from exc
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise DataUnavailableError(f"Approved data file is invalid: {self.data_path}: {exc}") from exc

        self.packages: dict[str, str] = dict(data.packages)
        self.saturday_availability: dict[str, str] = dict(data.saturday_availability)

    def lookup(self, analysis: EnquiryAnalysis) -> LookupResult:
        """Deterministically resolve facts from the approved data store only."""
        matched_packages: dict[str, str] = {}
        matched_slots: dict[str, str] = {}
        missing_items: list[str] = []

        if analysis.intent in ("out_of_scope", "unknown"):
            missing_items.append(f"intent:{analysis.intent}")

        for topic in analysis.details.unanswerable_topics:
            if topic.strip():
                missing_items.append(f"unanswerable:{topic.strip()}")

        for raw_pkg in analysis.details.requested_packages:
            norm = _normalize(raw_pkg)
            if not norm:
                continue
            if norm in ALL_PACKAGES_TOKENS:
                matched_packages.update(self.packages)
                continue

            canonical = PACKAGE_ALIASES.get(norm)
            if canonical and canonical in self.packages:
                matched_packages[canonical] = self.packages[canonical]
            else:
                missing_items.append(f"package:{raw_pkg}")

        for raw_slot in analysis.details.requested_slots:
            norm = _normalize(raw_slot)
            if not norm:
                continue
            if norm in ALL_SATURDAY_TOKENS:
                matched_slots.update(self.saturday_availability)
                continue

            canonical = SLOT_ALIASES.get(norm)
            if canonical and canonical in self.saturday_availability:
                matched_slots[canonical] = self.saturday_availability[canonical]
            else:
                missing_items.append(f"slot:{raw_slot}")

        # Handle general supported intents when the LLM omits explicit list tokens
        if not missing_items:
            if (
                analysis.intent in ("package_pricing", "pricing_and_availability")
                and not analysis.details.requested_packages
            ):
                matched_packages.update(self.packages)
            if (
                analysis.intent in ("saturday_availability", "pricing_and_availability")
                and not analysis.details.requested_slots
            ):
                matched_slots.update(self.saturday_availability)

        return LookupResult(
            matched_packages=matched_packages,
            matched_slots=matched_slots,
            missing_items=missing_items,
        )

"""Pydantic data models for the FitDesk Enquiry Assistant."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ESCALATION_MESSAGE = "I'll check with the owner and come back to you."
MAX_MESSAGE_CHARS = 1000

IntentType = Literal[
    "package_pricing",
    "saturday_availability",
    "pricing_and_availability",
    "out_of_scope",
    "unknown",
]

LogStatusType = Literal[
    "sent",
    "rejected",
    "escalated_not_sent",
    "error_invalid_ai_output",
    "error_ai_service_failure",
    "error_data_unavailable",
]


class EnquiryDetails(BaseModel):
    """Structured details extracted from a customer enquiry by the LLM."""

    requested_packages: list[str] = Field(
        default_factory=list,
        description=(
            "Packages asked about (e.g., 'Single session', '5 sessions', "
            "'12 sessions', 'all_packages', or an unrecognized package name)."
        ),
    )
    requested_slots: list[str] = Field(
        default_factory=list,
        description=(
            "Slots asked about (e.g., 'Saturday 09:00', 'Saturday 10:00', "
            "'Saturday 11:00', 'all_saturday', or an unrecognized day/time)."
        ),
    )
    unanswerable_topics: list[str] = Field(
        default_factory=list,
        description=(
            "Topics outside approved package pricing and Saturday availability "
            "(e.g., bookings, payments, discounts, Sunday slots, location)."
        ),
    )


class EnquiryAnalysis(BaseModel):
    """Validated structured JSON output expected from the Gemini API."""

    intent: IntentType
    details: EnquiryDetails
    requires_human_approval: bool


class StudioData(BaseModel):
    """Schema of the approved studio data file (data/studio_data.json)."""

    packages: dict[str, str] = Field(min_length=1)
    saturday_availability: dict[str, Literal["Available", "Full"]] = Field(min_length=1)

    @model_validator(mode="after")
    def _prices_are_rand_amounts(self) -> "StudioData":
        for name, price in self.packages.items():
            digits = price[1:].replace(",", "")
            if not (price.startswith("R") and digits.isdigit()):
                raise ValueError(f"Price for {name!r} must look like 'R1,680', got {price!r}")
        return self


class LookupResult(BaseModel):
    """Result of deterministic fact lookup against the approved local data file."""

    matched_packages: dict[str, str] = Field(default_factory=dict)
    matched_slots: dict[str, str] = Field(default_factory=dict)
    missing_items: list[str] = Field(default_factory=list)

    @property
    def has_missing_facts(self) -> bool:
        """Return True if any requested fact was missing or nothing matched."""
        return bool(self.missing_items) or (
            not self.matched_packages and not self.matched_slots
        )


class StatusLogEntry(BaseModel):
    """Audit record appended to status_log.json for every assistant run."""

    timestamp: str
    customer_message: str
    intent: str | None = None
    draft_reply: str | None = None
    needs_escalation: bool = False
    approved: bool = False
    status: LogStatusType
    error: str | None = None

    @model_validator(mode="after")
    def _sent_requires_approved_draft(self) -> "StatusLogEntry":
        """Core safety invariant: 'sent' if and only if staff approved an existing draft."""
        if self.status == "sent" and (not self.approved or not self.draft_reply):
            raise ValueError("status 'sent' requires approved=True and a draft_reply")
        if self.approved and self.status != "sent":
            raise ValueError("approved=True is only valid with status 'sent'")
        return self

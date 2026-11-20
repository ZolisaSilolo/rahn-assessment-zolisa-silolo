"""Core FitDesk Enquiry Assistant pipeline with mandatory human approval."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Callable

from src.data_store import StudioDataStore
from src.gemini_client import (
    AIServiceError,
    GeminiEnquiryParser,
    InvalidAIOutputError,
)
from src.models import ESCALATION_MESSAGE, MAX_MESSAGE_CHARS, LookupResult, StatusLogEntry

APPROVE_ANSWERS = {"y", "yes"}
DECLINE_ANSWERS = {"n", "no"}
MAX_APPROVAL_ATTEMPTS = 3


class StatusLogError(Exception):
    """Raised when status_log.json cannot be written. Nothing is recorded as sent."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_message(message: str | None) -> str:
    """Return the stripped message, or raise ValueError if it is empty or too long."""
    text = (message or "").strip()
    if not text:
        raise ValueError("No customer enquiry provided.")
    if len(text) > MAX_MESSAGE_CHARS:
        raise ValueError(
            f"Customer enquiry is {len(text)} characters; the limit is {MAX_MESSAGE_CHARS}."
        )
    return text


def draft_reply(lookup: LookupResult) -> tuple[str, bool]:
    """Draft a reply strictly from approved lookup facts or return exact escalation."""
    if lookup.has_missing_facts:
        return ESCALATION_MESSAGE, True

    parts: list[str] = []

    if lookup.matched_packages:
        pkg_items = ", ".join(
            f"{name}: {price}" for name, price in lookup.matched_packages.items()
        )
        parts.append(f"Session packages — {pkg_items}.")

    if lookup.matched_slots:
        slot_items = ", ".join(
            f"{slot}: {status}" for slot, status in lookup.matched_slots.items()
        )
        parts.append(f"Saturday availability — {slot_items}.")

    reply = "Hi there! " + " ".join(parts)
    return reply, False


def append_status_log(entry: StatusLogEntry, log_path: Path) -> Path | None:
    """Append an entry to the JSON-array log atomically.

    A corrupt existing log is moved aside (never silently overwritten) so audit history is kept.
    Returns the backup path if that happened, otherwise None.
    """
    log_path = Path(log_path)
    entries: list[dict] = []
    backup: Path | None = None

    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            existing = None
        if isinstance(existing, list):
            entries = existing
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = log_path.with_name(f"{log_path.stem}.corrupt-{stamp}{log_path.suffix}")
            os.replace(log_path, backup)

    entries.append(entry.model_dump())
    tmp = log_path.with_name(log_path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        os.replace(tmp, log_path)
    except OSError as exc:
        raise StatusLogError(f"Could not write {log_path}: {exc}") from exc
    return backup


def prompt_human_approval(
    draft: str,
    needs_escalation: bool,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    max_attempts: int = MAX_APPROVAL_ATTEMPTS,
) -> bool:
    """Display the draft and ask 'Send? (y/n)'. Anything but an explicit yes means not sent."""
    output_fn("\n--- Draft Reply ---")
    if needs_escalation:
        output_fn("[Notice: Missing approved facts — escalation message prepared]")
    output_fn(draft)
    output_fn("-------------------")

    for _ in range(max_attempts):
        try:
            answer = input_fn("Send? (y/n) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            output_fn("\nApproval interrupted. Not sent.")
            return False

        if answer in APPROVE_ANSWERS:
            return True
        if answer in DECLINE_ANSWERS:
            return False
        output_fn("Please enter 'y' or 'n'.")

    output_fn("No valid answer. Not sent.")
    return False


@dataclass
class PreparedDraft:
    """Outcome of the parse -> lookup -> draft stage, before any human decision."""

    customer_message: str
    intent: str | None = None
    draft_reply: str | None = None
    needs_escalation: bool = False
    failure_status: str | None = None  # set when no draft could be produced
    error: str | None = None

    def to_entry(self, approved: bool) -> StatusLogEntry:
        """Build the audit entry. 'sent' only for an existing draft that a human approved."""
        if self.failure_status:
            return StatusLogEntry(
                timestamp=now_iso(),
                customer_message=self.customer_message,
                status=self.failure_status,
                error=self.error,
            )
        approved = bool(approved and self.draft_reply)
        if approved:
            status = "sent"
        elif self.needs_escalation:
            status = "escalated_not_sent"
        else:
            status = "rejected"
        return StatusLogEntry(
            timestamp=now_iso(),
            customer_message=self.customer_message,
            intent=self.intent,
            draft_reply=self.draft_reply,
            needs_escalation=self.needs_escalation,
            approved=approved,
            status=status,
        )


class FitDeskAssistant:
    """End-to-end CLI assistant implementing RAHN's 6-layer safety framework."""

    def __init__(
        self,
        parser: GeminiEnquiryParser,
        data_store: StudioDataStore,
        log_path: Path,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.parser = parser
        self.data_store = data_store
        self.log_path = Path(log_path)
        self.input_fn = input_fn
        self.output_fn = output_fn

    def prepare_draft(self, customer_message: str) -> PreparedDraft:
        """Layers 02-03: parse with Gemini, validate, look up approved facts, draft."""
        try:
            analysis = self.parser.parse_enquiry(customer_message)
        except InvalidAIOutputError as exc:
            return PreparedDraft(customer_message, failure_status="error_invalid_ai_output", error=str(exc))
        except AIServiceError as exc:
            return PreparedDraft(customer_message, failure_status="error_ai_service_failure", error=str(exc))

        lookup = self.data_store.lookup(analysis)
        draft, needs_escalation = draft_reply(lookup)
        return PreparedDraft(
            customer_message,
            intent=analysis.intent,
            draft_reply=draft,
            needs_escalation=needs_escalation,
        )

    def record(self, entry: StatusLogEntry) -> StatusLogEntry:
        """Layer 05: persist the outcome to the local status log."""
        backup = append_status_log(entry, self.log_path)
        if backup:
            self.output_fn(f"[Warning] Existing status log was corrupt; moved to {backup.name}.")
        return entry

    def handle_enquiry(self, customer_message: str) -> StatusLogEntry:
        """Process one customer enquiry from parsing through approval and logging."""
        prepared = self.prepare_draft(customer_message)

        if prepared.failure_status:
            label = {
                "error_invalid_ai_output": "Invalid AI output",
                "error_ai_service_failure": "AI service unavailable",
            }[prepared.failure_status]
            self.output_fn(f"\n[Error] {label}: {prepared.error}")
            self.output_fn("Please answer this customer manually. Reply was NOT sent.")
            entry = self.record(prepared.to_entry(approved=False))
            self.output_fn(f"Recorded status: {entry.status} (saved to {self.log_path.name})")
            return entry

        # Layer 04: mandatory human approval gate, enforced whatever the LLM said
        approved = prompt_human_approval(
            draft=prepared.draft_reply or "",
            needs_escalation=prepared.needs_escalation,
            input_fn=self.input_fn,
            output_fn=self.output_fn,
        )
        entry = self.record(prepared.to_entry(approved=approved))
        self.output_fn(f"Recorded status: {entry.status} (saved to {self.log_path.name})")
        return entry

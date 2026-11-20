"""Automated regression tests for FitDesk Enquiry Assistant (Gemini mocked).

All tests run completely offline without a Gemini API key or live API calls.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.assistant import FitDeskAssistant
from src.data_store import StudioDataStore
from src.gemini_client import AIServiceError, GeminiEnquiryParser
from src.models import ESCALATION_MESSAGE

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "studio_data.json"


def _read_log(log_path: Path) -> list[dict]:
    assert log_path.exists(), "Expected status_log.json to be created"
    return json.loads(log_path.read_text(encoding="utf-8"))


@pytest.fixture
def data_store() -> StudioDataStore:
    return StudioDataStore(DATA_PATH)


def test_rt01_normal_enquiry_answered_from_approved_data(
    data_store: StudioDataStore, tmp_path: Path
) -> None:
    """RT-01 (FR-01, FR-02, FR-03, FR-04): Normal enquiry answered strictly from data."""
    mock_json = json.dumps(
        {
            "intent": "pricing_and_availability",
            "details": {
                "requested_packages": ["12 sessions"],
                "requested_slots": ["all_saturday"],
                "unanswerable_topics": [],
            },
            "requires_human_approval": True,
        }
    )
    parser = GeminiEnquiryParser(raw_call_fn=lambda _msg: mock_json)
    log_path = tmp_path / "status_log.json"
    outputs: list[str] = []

    assistant = FitDeskAssistant(
        parser=parser,
        data_store=data_store,
        log_path=log_path,
        input_fn=lambda _prompt: "y",
        output_fn=outputs.append,
    )

    entry = assistant.handle_enquiry(
        "How much is the 12-session pack and do you have Saturday slots?"
    )

    assert entry.needs_escalation is False
    assert entry.draft_reply is not None
    assert "R1,680" in entry.draft_reply
    assert "Saturday 09:00: Available" in entry.draft_reply
    assert "Saturday 10:00: Full" in entry.draft_reply
    assert "Saturday 11:00: Available" in entry.draft_reply
    assert entry.status == "sent"


def test_rt02_missing_fact_triggers_exact_escalation_and_blocks_unapproved_send(
    data_store: StudioDataStore, tmp_path: Path
) -> None:
    """RT-02 (FR-03, FR-04, FR-06): Missing fact yields exact escalation phrase."""
    mock_json = json.dumps(
        {
            "intent": "package_pricing",
            "details": {
                "requested_packages": ["20 sessions"],
                "requested_slots": [],
                "unanswerable_topics": [],
            },
            "requires_human_approval": True,
        }
    )
    parser = GeminiEnquiryParser(raw_call_fn=lambda _msg: mock_json)
    log_path = tmp_path / "status_log.json"

    # Case A: Staff declines sending escalation -> must NOT be recorded as 'sent'
    assistant_decline = FitDeskAssistant(
        parser=parser,
        data_store=data_store,
        log_path=log_path,
        input_fn=lambda _prompt: "n",
        output_fn=lambda _msg: None,
    )
    declined_entry = assistant_decline.handle_enquiry("How much is a 20-session pack?")

    assert declined_entry.draft_reply == ESCALATION_MESSAGE
    assert declined_entry.needs_escalation is True
    assert declined_entry.approved is False
    assert declined_entry.status == "escalated_not_sent"
    assert declined_entry.status != "sent"

    # Case B: Staff explicitly approves sending escalation -> allowed to log 'sent'
    assistant_approve = FitDeskAssistant(
        parser=parser,
        data_store=data_store,
        log_path=log_path,
        input_fn=lambda _prompt: "y",
        output_fn=lambda _msg: None,
    )
    approved_entry = assistant_approve.handle_enquiry("How much is a 20-session pack?")

    assert approved_entry.draft_reply == ESCALATION_MESSAGE
    assert approved_entry.needs_escalation is True
    assert approved_entry.approved is True
    assert approved_entry.status == "sent"


@pytest.mark.parametrize(
    "bad_payload",
    [
        "{broken json without closing brace",
        json.dumps({"intent": "package_pricing"}),  # missing required Pydantic fields
        json.dumps(
            {
                "intent": "not_a_valid_intent",
                "details": {},
                "requires_human_approval": True,
            }
        ),
    ],
)
def test_rt03_invalid_or_broken_ai_json_never_records_sent(
    data_store: StudioDataStore, tmp_path: Path, bad_payload: str
) -> None:
    """RT-03 (FR-02, NFR-02): Broken/invalid JSON fails Pydantic and never sends."""
    parser = GeminiEnquiryParser(raw_call_fn=lambda _msg: bad_payload)
    log_path = tmp_path / "status_log.json"
    mock_input = MagicMock(return_value="y")

    assistant = FitDeskAssistant(
        parser=parser,
        data_store=data_store,
        log_path=log_path,
        input_fn=mock_input,
        output_fn=lambda _msg: None,
    )

    entry = assistant.handle_enquiry("How much is a single session?")

    mock_input.assert_not_called()
    assert entry.approved is False
    assert entry.status == "error_invalid_ai_output"
    assert entry.status != "sent"
    logged = _read_log(log_path)
    assert logged[-1]["status"] == "error_invalid_ai_output"


def test_rt04_ai_service_failure_never_records_sent(
    data_store: StudioDataStore, tmp_path: Path
) -> None:
    """RT-04 (NFR-02): AI service exception/timeout is caught and never sends."""

    def failing_api(_msg: str) -> str:
        raise TimeoutError("Simulated Gemini API timeout")

    parser = GeminiEnquiryParser(raw_call_fn=failing_api)
    log_path = tmp_path / "status_log.json"
    mock_input = MagicMock(return_value="y")

    assistant = FitDeskAssistant(
        parser=parser,
        data_store=data_store,
        log_path=log_path,
        input_fn=mock_input,
        output_fn=lambda _msg: None,
    )

    entry = assistant.handle_enquiry("Are Saturday slots open?")

    mock_input.assert_not_called()
    assert entry.approved is False
    assert entry.status == "error_ai_service_failure"
    assert entry.status != "sent"
    assert entry.error is not None
    logged = _read_log(log_path)
    assert logged[-1]["status"] == "error_ai_service_failure"


def test_rt05_staff_says_no_at_approval_never_records_sent(
    data_store: StudioDataStore, tmp_path: Path
) -> None:
    """RT-05 (FR-05, FR-06): Staff 'n' rejects draft even if model sets approval=false."""
    mock_json = json.dumps(
        {
            "intent": "package_pricing",
            "details": {
                "requested_packages": ["Single session"],
                "requested_slots": [],
                "unanswerable_topics": [],
            },
            "requires_human_approval": False,  # System must still enforce approval
        }
    )
    parser = GeminiEnquiryParser(raw_call_fn=lambda _msg: mock_json)
    log_path = tmp_path / "status_log.json"

    assistant = FitDeskAssistant(
        parser=parser,
        data_store=data_store,
        log_path=log_path,
        input_fn=lambda _prompt: "n",
        output_fn=lambda _msg: None,
    )

    entry = assistant.handle_enquiry("How much is a single session?")

    assert entry.draft_reply is not None
    assert "R180" in entry.draft_reply
    assert entry.approved is False
    assert entry.status == "rejected"
    logged = _read_log(log_path)
    assert logged[-1]["status"] == "rejected"


def test_rt06_staff_says_yes_at_approval_records_sent(
    data_store: StudioDataStore, tmp_path: Path
) -> None:
    """RT-06 (FR-05, FR-06): Staff 'y' approves valid draft and logs 'sent'."""
    mock_json = json.dumps(
        {
            "intent": "saturday_availability",
            "details": {
                "requested_packages": [],
                "requested_slots": ["Saturday 09:00", "Saturday 10:00"],
                "unanswerable_topics": [],
            },
            "requires_human_approval": True,
        }
    )
    parser = GeminiEnquiryParser(raw_call_fn=lambda _msg: mock_json)
    log_path = tmp_path / "status_log.json"

    assistant = FitDeskAssistant(
        parser=parser,
        data_store=data_store,
        log_path=log_path,
        input_fn=lambda _prompt: "y",
        output_fn=lambda _msg: None,
    )

    entry = assistant.handle_enquiry("Is Saturday 09:00 or 10:00 available?")

    assert entry.approved is True
    assert entry.status == "sent"
    assert entry.draft_reply is not None
    assert "Saturday 09:00: Available" in entry.draft_reply
    assert "Saturday 10:00: Full" in entry.draft_reply
    logged = _read_log(log_path)
    assert len(logged) == 1
    assert logged[0]["status"] == "sent"
    assert logged[0]["approved"] is True


def test_rt07_mixed_known_and_unknown_enquiry_escalates_without_guessing(
    data_store: StudioDataStore, tmp_path: Path
) -> None:
    """RT-07 (FR-04, Assumption 1): Mixed enquiry with missing Sunday slot escalates."""
    mock_json = json.dumps(
        {
            "intent": "pricing_and_availability",
            "details": {
                "requested_packages": ["5 sessions"],
                "requested_slots": ["Sunday 09:00"],
                "unanswerable_topics": [],
            },
            "requires_human_approval": True,
        }
    )
    parser = GeminiEnquiryParser(raw_call_fn=lambda _msg: mock_json)
    log_path = tmp_path / "status_log.json"

    assistant = FitDeskAssistant(
        parser=parser,
        data_store=data_store,
        log_path=log_path,
        input_fn=lambda _prompt: "n",
        output_fn=lambda _msg: None,
    )

    entry = assistant.handle_enquiry("How much is 5 sessions and are you open Sunday 09:00?")

    assert entry.draft_reply == ESCALATION_MESSAGE
    assert entry.needs_escalation is True
    assert entry.status == "escalated_not_sent"


def test_rt08_mocked_gemini_sdk_runs_without_api_key(
    data_store: StudioDataStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RT-08 (NFR-03): Patched GeminiEnquiryParser._call_gemini works with no API key."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    mock_json = json.dumps(
        {
            "intent": "package_pricing",
            "details": {
                "requested_packages": ["5 sessions"],
                "requested_slots": [],
                "unanswerable_topics": [],
            },
            "requires_human_approval": True,
        }
    )

    with patch.object(GeminiEnquiryParser, "_call_gemini", return_value=mock_json):
        parser = GeminiEnquiryParser()
        assistant = FitDeskAssistant(
            parser=parser,
            data_store=data_store,
            log_path=tmp_path / "status_log.json",
            input_fn=lambda _prompt: "y",
            output_fn=lambda _msg: None,
        )
        entry = assistant.handle_enquiry("What is the 5-session price?")

    assert entry.status == "sent"
    assert entry.draft_reply is not None
    assert "R800" in entry.draft_reply


def test_rt09_general_intent_with_empty_detail_lists_uses_approved_data(
    data_store: StudioDataStore, tmp_path: Path
) -> None:
    """RT-09 (FR-03, FR-04): General Saturday enquiry with empty slot list still returns approved slots."""
    mock_json = json.dumps(
        {
            "intent": "saturday_availability",
            "details": {
                "requested_packages": [],
                "requested_slots": [],
                "unanswerable_topics": [],
            },
            "requires_human_approval": True,
        }
    )
    parser = GeminiEnquiryParser(raw_call_fn=lambda _msg: mock_json)
    assistant = FitDeskAssistant(
        parser=parser,
        data_store=data_store,
        log_path=tmp_path / "status_log.json",
        input_fn=lambda _prompt: "y",
        output_fn=lambda _msg: None,
    )

    entry = assistant.handle_enquiry("Do you have Saturday slots?")

    assert entry.needs_escalation is False
    assert entry.draft_reply is not None
    assert "Saturday 09:00: Available" in entry.draft_reply
    assert "Saturday 10:00: Full" in entry.draft_reply
    assert "Saturday 11:00: Available" in entry.draft_reply
    assert entry.status == "sent"


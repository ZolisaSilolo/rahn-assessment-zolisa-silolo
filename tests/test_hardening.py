"""Hardening regression tests (RT-10..RT-18). Gemini is always faked: no API key, no network."""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

import src.gemini_client as gemini_client
import src.main as cli
from src.assistant import FitDeskAssistant, draft_reply, validate_message
from src.data_store import DataUnavailableError, StudioDataStore
from src.gemini_client import (
    GEMINI_TIMEOUT_MS,
    AIServiceError,
    GeminiEnquiryParser,
    InvalidAIOutputError,
)
from src.models import ESCALATION_MESSAGE, MAX_MESSAGE_CHARS, EnquiryAnalysis, StatusLogEntry

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "studio_data.json"


def analysis(intent="package_pricing", packages=(), slots=(), topics=(), approval=True) -> dict:
    return {
        "intent": intent,
        "details": {
            "requested_packages": list(packages),
            "requested_slots": list(slots),
            "unanswerable_topics": list(topics),
        },
        "requires_human_approval": approval,
    }


def fake_parser(payload: dict) -> GeminiEnquiryParser:
    return GeminiEnquiryParser(raw_call_fn=lambda _msg: json.dumps(payload))


def make_assistant(parser, log_path, answers=("y",)):
    replies = iter(answers)
    input_fn = MagicMock(side_effect=lambda _prompt: next(replies))
    assistant = FitDeskAssistant(
        parser=parser,
        data_store=StudioDataStore(DATA_PATH),
        log_path=log_path,
        input_fn=input_fn,
        output_fn=lambda _msg: None,
    )
    return assistant, input_fn


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: False)


def test_rt10_missing_api_key_uses_real_client_path_and_never_prompts(tmp_path):
    """RT-10 (FR-02, NFR-01, NFR-02): the path the CLI hits with no key fails safe."""
    assistant, input_fn = make_assistant(GeminiEnquiryParser(), tmp_path / "log.json")
    entry = assistant.handle_enquiry("How much is a single session?")
    input_fn.assert_not_called()
    assert entry.status == "error_ai_service_failure"
    assert "GEMINI_API_KEY" in (entry.error or "")


def test_rt11_gemini_call_has_timeout_and_bad_responses_fail_safe(monkeypatch):
    """RT-11 (NFR-02): the SDK client gets a timeout; empty text and SDK errors never pass."""
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.models = SimpleNamespace(generate_content=lambda **_: SimpleNamespace(text=""))

    monkeypatch.setattr(gemini_client.genai, "Client", FakeClient)
    with pytest.raises(InvalidAIOutputError):
        GeminiEnquiryParser(api_key="test-key").parse_enquiry("hi")
    assert seen["http_options"].timeout == GEMINI_TIMEOUT_MS

    class FailingClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.models = SimpleNamespace(generate_content=MagicMock(side_effect=TimeoutError("read timeout")))

    monkeypatch.setattr(gemini_client.genai, "Client", FailingClient)
    with pytest.raises(AIServiceError):
        GeminiEnquiryParser(api_key="test-key").parse_enquiry("hi")


def test_rt12_corrupt_status_log_is_backed_up_not_wiped(tmp_path):
    """RT-12 (FR-06): a corrupt log keeps its audit history in a backup file."""
    log_path = tmp_path / "status_log.json"
    original = json.dumps([{"status": "sent", "note": "earlier audit history"}])[:-1]  # truncated
    log_path.write_text(original, encoding="utf-8")

    assistant, _ = make_assistant(fake_parser(analysis(packages=["Single session"])), log_path)
    assistant.handle_enquiry("How much is a single session?")

    backups = list(tmp_path.glob("status_log.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    assert len(json.loads(log_path.read_text(encoding="utf-8"))) == 1


@pytest.mark.parametrize("data_text", [None, "{oops", json.dumps({"packages": {}})])
def test_rt13_missing_or_invalid_data_logs_error_and_never_calls_gemini(tmp_path, monkeypatch, data_text):
    """RT-13 (FR-03, NFR-02): bad data stops before any AI call; no traceback; logged."""
    data_path = tmp_path / "studio_data.json"
    if data_text is not None:
        data_path.write_text(data_text, encoding="utf-8")
    monkeypatch.setattr(cli, "GeminiEnquiryParser", MagicMock(side_effect=AssertionError("Gemini must not be used")))
    log_path = tmp_path / "status_log.json"

    code = cli.main(["--message", "How much is 5 sessions?", "--data", str(data_path), "--log", str(log_path)])

    assert code == 1
    assert json.loads(log_path.read_text(encoding="utf-8"))[-1]["status"] == "error_data_unavailable"


@pytest.mark.parametrize("message", ["", "   ", "x" * (MAX_MESSAGE_CHARS + 1)])
def test_rt14_empty_or_oversized_message_rejected_before_ai(tmp_path, monkeypatch, message):
    """RT-14 (FR-01, NFR-03): invalid input never reaches Gemini and is not logged."""
    monkeypatch.setattr(cli, "GeminiEnquiryParser", MagicMock(side_effect=AssertionError("Gemini must not be used")))
    monkeypatch.setattr("builtins.input", lambda _prompt: message)
    log_path = tmp_path / "status_log.json"
    assert cli.main(["--message", message, "--log", str(log_path)]) == 1
    assert not log_path.exists()
    assert validate_message("x" * MAX_MESSAGE_CHARS) == "x" * MAX_MESSAGE_CHARS


def test_rt15_status_entry_rejects_sent_without_approved_draft():
    """RT-15 (FR-06): the model itself forbids an unapproved or draftless 'sent'."""
    with pytest.raises(ValidationError):
        StatusLogEntry(timestamp="t", customer_message="m", status="sent", approved=False, draft_reply="d")
    with pytest.raises(ValidationError):
        StatusLogEntry(timestamp="t", customer_message="m", status="sent", approved=True, draft_reply=None)
    with pytest.raises(ValidationError):
        StatusLogEntry(timestamp="t", customer_message="m", status="rejected", approved=True, draft_reply="d")


@pytest.mark.parametrize(
    "answers, expected_status, expected_prompts",
    [
        (("YES",), "sent", 1),
        (("No",), "rejected", 1),
        (("maybe", "?", "ok"), "rejected", 3),
    ],
)
def test_rt16_approval_answers(tmp_path, answers, expected_status, expected_prompts):
    """RT-16 (FR-05): yes/no variants work; three invalid answers mean not sent."""
    assistant, input_fn = make_assistant(fake_parser(analysis(packages=["5 sessions"])), tmp_path / "l.json", answers)
    entry = assistant.handle_enquiry("How much is 5 sessions?")
    assert entry.status == expected_status
    assert input_fn.call_count == expected_prompts


def test_rt16b_eof_at_approval_is_not_sent(tmp_path):
    """RT-16b (FR-05): Ctrl+D / closed stdin at the prompt is treated as not sent."""
    assistant, _ = make_assistant(fake_parser(analysis(packages=["5 sessions"])), tmp_path / "l.json")
    assistant.input_fn = MagicMock(side_effect=EOFError)
    assert assistant.handle_enquiry("How much is 5 sessions?").status == "rejected"


def test_rt17_bare_time_without_saturday_escalates(tmp_path):
    """RT-17 (FR-04): '09:00' with no day is not assumed to be Saturday."""
    assistant, _ = make_assistant(
        fake_parser(analysis(intent="saturday_availability", slots=["09:00"])), tmp_path / "l.json", ("n",)
    )
    entry = assistant.handle_enquiry("Is 09:00 free on Sunday?")
    assert entry.draft_reply == ESCALATION_MESSAGE
    assert entry.status == "escalated_not_sent"


@pytest.mark.parametrize(
    "payload",
    [
        analysis(packages=["all_packages"]),
        analysis(intent="pricing_and_availability", packages=["12 sessions"], slots=["all_saturday"]),
        analysis(intent="saturday_availability", slots=["Saturday 11:00"]),
        analysis(packages=["20 sessions"]),
        analysis(intent="out_of_scope", topics=["yoga classes"]),
    ],
)
def test_rt18_every_fact_in_a_draft_comes_from_the_data_file(payload):
    """RT-18 (Rule 1, FR-04, NFR-01): no price or time in any draft is absent from the data file."""
    store = StudioDataStore(DATA_PATH)
    draft, _ = draft_reply(store.lookup(EnquiryAnalysis.model_validate(payload)))
    approved_prices = set(store.packages.values())
    approved_times = {slot.split()[-1] for slot in store.saturday_availability}
    assert set(re.findall(r"R[\d,]*\d", draft)) <= approved_prices
    assert set(re.findall(r"\b\d{2}:\d{2}\b", draft)) <= approved_times


def test_rt19_data_file_schema_is_validated(tmp_path):
    """RT-19 (FR-03): malformed prices or slot statuses are rejected at load time."""
    bad_price = {"packages": {"Single session": "180 rand"}, "saturday_availability": {"Saturday 09:00": "Available"}}
    bad_status = {"packages": {"Single session": "R180"}, "saturday_availability": {"Saturday 09:00": "Maybe"}}
    for i, data in enumerate((bad_price, bad_status)):
        path = tmp_path / f"bad{i}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(DataUnavailableError):
            StudioDataStore(path)

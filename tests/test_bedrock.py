"""Bedrock provider tests (BR-01..BR-06). The bedrock-runtime client is faked: no AWS calls."""

import json
from pathlib import Path

import pytest

import src.main as cli
from src.assistant import FitDeskAssistant
from src.bedrock_client import DEFAULT_BEDROCK_MODEL, MAX_OUTPUT_TOKENS, TOOL_NAME, BedrockEnquiryParser, inline_schema
from src.data_store import StudioDataStore
from src.gemini_client import AIServiceError, GeminiEnquiryParser, InvalidAIOutputError
from src.models import EnquiryAnalysis

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "studio_data.json"
GOOD = {"intent": "pricing_and_availability",
        "details": {"requested_packages": ["12 sessions"], "requested_slots": ["all_saturday"],
                    "unanswerable_topics": []},
        "requires_human_approval": True}


class FakeBedrock:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def tool_response(tool_input):
    return {"output": {"message": {"role": "assistant",
                                   "content": [{"toolUse": {"toolUseId": "t1", "name": TOOL_NAME, "input": tool_input}}]}},
            "stopReason": "tool_use"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("LLM_PROVIDER", "BEDROCK_MODEL_ID", "BEDROCK_REGION", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_br01_valid_tool_output_is_validated_and_request_is_bounded():
    """BR-01 (FR-02, NFR-03): forced tool call, explicit maxTokens, Pydantic-validated result."""
    fake = FakeBedrock(tool_response(GOOD))
    analysis = BedrockEnquiryParser(client=fake).parse_enquiry("12 sessions and Saturday?")
    assert isinstance(analysis, EnquiryAnalysis) and analysis.intent == "pricing_and_availability"
    call = fake.calls[0]
    assert call["modelId"] == DEFAULT_BEDROCK_MODEL
    assert call["inferenceConfig"]["maxTokens"] == MAX_OUTPUT_TOKENS
    assert call["toolConfig"]["toolChoice"] == {"tool": {"name": TOOL_NAME}}
    assert len(fake.calls) == 1


def test_br02_bedrock_errors_become_ai_service_errors():
    """BR-02 (NFR-02): access denied, throttling or timeout fail safe."""
    with pytest.raises(AIServiceError):
        BedrockEnquiryParser(client=FakeBedrock(error=TimeoutError("read timeout"))).parse_enquiry("hi")


@pytest.mark.parametrize("response", [
    tool_response({"intent": "package_pricing"}),               # schema mismatch
    tool_response({**GOOD, "intent": "make_a_booking"}),        # invalid enum
    {"output": {"message": {"role": "assistant", "content": []}}},  # nothing returned
    {"output": {"message": {"role": "assistant", "content": [{"text": "Sure! It costs R10."}]}}},  # prose
])
def test_br03_invalid_model_output_is_rejected(response):
    """BR-03 (FR-02): anything that is not a valid EnquiryAnalysis is rejected."""
    with pytest.raises(InvalidAIOutputError):
        BedrockEnquiryParser(client=FakeBedrock(response)).parse_enquiry("hi")


def test_br04_end_to_end_draft_comes_from_data_file(tmp_path):
    """BR-04 (FR-04, FR-05): the Bedrock path produces the same data-only draft and approval gate."""
    assistant = FitDeskAssistant(
        parser=BedrockEnquiryParser(client=FakeBedrock(tool_response(GOOD))),
        data_store=StudioDataStore(DATA_PATH), log_path=tmp_path / "log.json",
        input_fn=lambda _p: "y", output_fn=lambda _m: None)
    entry = assistant.handle_enquiry("How much is the 12-session pack and do you have Saturday slots?")
    assert entry.status == "sent" and "R1,680" in entry.draft_reply and "Saturday 10:00: Full" in entry.draft_reply


def test_br05_provider_selection(monkeypatch):
    """BR-05: Gemini by default (brief); Bedrock via --provider or LLM_PROVIDER."""
    assert type(cli.build_llm_parser(None)) is GeminiEnquiryParser
    assert isinstance(cli.build_llm_parser("bedrock"), BedrockEnquiryParser)
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    assert isinstance(cli.build_llm_parser(None), BedrockEnquiryParser)


def test_br06_tool_schema_is_self_contained():
    """BR-06: no $ref/$defs left in the tool input schema."""
    text = json.dumps(inline_schema(EnquiryAnalysis))
    assert "$ref" not in text and "$defs" not in text and "requires_human_approval" in text

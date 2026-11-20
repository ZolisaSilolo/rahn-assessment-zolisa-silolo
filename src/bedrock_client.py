"""Optional Amazon Bedrock provider (Converse API) returning the same validated EnquiryAnalysis.

Gemini stays the default provider required by the brief. Bedrock is selected with
`--provider bedrock` or LLM_PROVIDER=bedrock and uses your normal AWS credentials (no API key file).

Structured output: the model is forced to call a single tool whose input schema is the
EnquiryAnalysis schema, then the tool input is validated by Pydantic exactly like Gemini's JSON.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

from src.gemini_client import SYSTEM_PROMPT, AIServiceError, GeminiEnquiryParser, InvalidAIOutputError
from src.models import EnquiryAnalysis

# Amazon Nova Micro supports on-demand invocation in us-east-1 with its base model ID, so requests
# stay in the chosen Region (no cross-Region inference profile). Override with BEDROCK_MODEL_ID.
DEFAULT_BEDROCK_MODEL = "amazon.nova-micro-v1:0"
DEFAULT_BEDROCK_REGION = "us-east-1"
BEDROCK_READ_TIMEOUT_S = 20
MAX_OUTPUT_TOKENS = 512  # always explicit: an unset maxTokens reserves the model maximum
TOOL_NAME = "record_enquiry_analysis"


def inline_schema(model: type) -> dict:
    """JSON schema with $defs/$ref resolved inline (tool schemas should be self-contained)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


class BedrockEnquiryParser(GeminiEnquiryParser):
    """Same contract as GeminiEnquiryParser; only the model call differs."""

    def __init__(self, model_id: str | None = None, region: str | None = None, client: Any = None,
                 raw_call_fn=None) -> None:
        super().__init__(api_key="unused-for-bedrock", raw_call_fn=raw_call_fn)
        self.model = model_id or os.getenv("BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL
        self.region = region or os.getenv("BEDROCK_REGION") or DEFAULT_BEDROCK_REGION
        self._client = client

    def _bedrock(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
                config=Config(connect_timeout=5, read_timeout=BEDROCK_READ_TIMEOUT_S,
                              retries={"max_attempts": 1, "mode": "standard"}),  # one call per enquiry
            )
        return self._client

    # GeminiEnquiryParser.parse_enquiry() calls this method; overriding it swaps the provider
    # while keeping the shared validation path (Pydantic) unchanged.
    def _call_gemini(self, customer_message: str) -> str:  # noqa: D401 - provider hook
        try:
            response = self._bedrock().converse(
                modelId=self.model,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": customer_message}]}],
                inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
                toolConfig={
                    "tools": [{"toolSpec": {
                        "name": TOOL_NAME,
                        "description": "Record the structured analysis of the customer enquiry.",
                        "inputSchema": {"json": inline_schema(EnquiryAnalysis)},
                    }}],
                    "toolChoice": {"tool": {"name": TOOL_NAME}},
                },
            )
        except Exception as exc:  # noqa: BLE001 - credentials, access, throttling, timeouts
            raise AIServiceError(f"Bedrock call failed: {type(exc).__name__}: {exc}") from exc

        content = (((response or {}).get("output") or {}).get("message") or {}).get("content") or []
        for block in content:
            if "toolUse" in block:
                return json.dumps(block["toolUse"].get("input"))
        text = "".join(block.get("text", "") for block in content).strip()
        if not text:
            raise InvalidAIOutputError("Bedrock returned no tool output and no text.")
        return text

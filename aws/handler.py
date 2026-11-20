"""AWS Lambda front end for the FitDesk Enquiry Assistant.

Served by a Lambda Function URL with AuthType AWS_IAM: only SigV4-signed requests from authorised
staff identities reach this code. The safety logic is the CLI's, reused unchanged:

  POST /enquiries                 {"message": "..."}   -> draft stored as pending_approval (201)
  POST /enquiries/{id}/decision   {"approve": true|false} -> sent / rejected / escalated_not_sent
  GET  /enquiries/{id}                                  -> the stored record
  GET  /health

A draft can be decided once only (DynamoDB conditional update), so nothing can be "sent" twice
and an AI or data failure can never be approved.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from src.assistant import FitDeskAssistant, PreparedDraft, now_iso, validate_message
from src.data_store import DataUnavailableError, StudioDataStore
from src.gemini_client import GeminiEnquiryParser

DATA_PATH = Path(__file__).resolve().parent / "data" / "studio_data.json"
PENDING = "pending_approval"
RETENTION_DAYS = 30  # records expire via DynamoDB TTL (POPIA: keep personal data no longer than needed)

_cache: dict[str, Any] = {}


# ---------------------------------------------------------------- AWS wiring (lazy imports)

def _table():
    if "table" not in _cache:
        import boto3  # provided by the Lambda Python runtime

        _cache["table"] = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _cache["table"]


def _gemini_api_key() -> str:
    """Read the key from SSM Parameter Store (SecureString). Failures yield '' -> fail-safe."""
    if "key" not in _cache:
        import boto3

        try:
            resp = boto3.client("ssm").get_parameter(
                Name=os.environ["GEMINI_API_KEY_PARAM"], WithDecryption=True
            )
            _cache["key"] = resp["Parameter"]["Value"]
        except Exception as exc:  # noqa: BLE001 - any failure must fail safe, not crash
            print(json.dumps({"level": "ERROR", "msg": "gemini key unavailable", "error": type(exc).__name__}))
            return ""
    return _cache["key"]


def build_parser() -> GeminiEnquiryParser:
    """Bedrock (default on AWS: IAM role, no key) or Gemini (key from SSM Parameter Store)."""
    if (os.environ.get("LLM_PROVIDER") or "bedrock").lower() == "bedrock":
        from src.bedrock_client import BedrockEnquiryParser

        return BedrockEnquiryParser(
            model_id=os.environ.get("BEDROCK_MODEL_ID") or None,
            region=os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or None,
        )
    return GeminiEnquiryParser(api_key=_gemini_api_key(), model=os.environ.get("GEMINI_MODEL") or None)


def lambda_handler(event: dict, context: Any) -> dict:
    try:
        response = handle(event, table=_table(), parser_factory=build_parser)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"level": "ERROR", "msg": "unhandled", "error": type(exc).__name__}))
        response = _resp(500, {"error": "Internal error. Nothing was sent."})
    # Log the route and outcome only, never message content (personal information).
    http = (event.get("requestContext") or {}).get("http") or {}
    print(json.dumps({"level": "INFO", "method": http.get("method"), "path": event.get("rawPath"),
                      "statusCode": response["statusCode"]}))
    return response


# ---------------------------------------------------------------- pure request handling

def _resp(code: int, body: dict) -> dict:
    def default(value):
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        raise TypeError(type(value).__name__)

    return {"statusCode": code, "headers": {"content-type": "application/json"},
            "body": json.dumps(body, default=default)}


def _body(event: dict) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    return data


def _caller(event: dict) -> str:
    iam = ((event.get("requestContext") or {}).get("authorizer") or {}).get("iam") or {}
    return iam.get("userArn") or "unknown"


def _is_conditional_failure(exc: Exception) -> bool:
    return (getattr(exc, "response", None) or {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _expiry() -> int:
    return int((datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS)).timestamp())


def _create(body: dict, table, parser_factory: Callable[[], GeminiEnquiryParser], data_path: Path) -> dict:
    message = body.get("message")
    message = validate_message(message if isinstance(message, str) else None)  # ValueError -> 400
    enquiry_id = str(uuid.uuid4())

    try:
        store = StudioDataStore(data_path)
    except DataUnavailableError as exc:
        entry = PreparedDraft(message, failure_status="error_data_unavailable", error=str(exc)).to_entry(False)
        table.put_item(Item={**entry.model_dump(), "id": enquiry_id, "expires_at": _expiry()})
        return _resp(503, {"id": enquiry_id, "status": entry.status, "error": "Studio data unavailable. Answer manually."})

    assistant = FitDeskAssistant(parser=parser_factory(), data_store=store, log_path=Path("/tmp/unused.json"))
    prepared = assistant.prepare_draft(message)

    if prepared.failure_status:
        entry = prepared.to_entry(approved=False)
        table.put_item(Item={**entry.model_dump(), "id": enquiry_id, "expires_at": _expiry()})
        return _resp(502, {"id": enquiry_id, "status": entry.status, "error": "AI step failed. Answer manually.",
                           "detail": entry.error})

    table.put_item(
        Item={
            "id": enquiry_id,
            "timestamp": now_iso(),
            "customer_message": message,
            "intent": prepared.intent,
            "draft_reply": prepared.draft_reply,
            "needs_escalation": prepared.needs_escalation,
            "approved": False,
            "status": PENDING,
            "expires_at": _expiry(),
        },
        ConditionExpression="attribute_not_exists(id)",
    )
    return _resp(201, {
        "id": enquiry_id,
        "status": PENDING,
        "draft_reply": prepared.draft_reply,
        "needs_escalation": prepared.needs_escalation,
        "next": f'POST /enquiries/{enquiry_id}/decision with {{"approve": true}} or {{"approve": false}}',
    })


def _decide(enquiry_id: str, body: dict, table, caller: str) -> dict:
    approve = body.get("approve")
    if not isinstance(approve, bool):
        raise ValueError('"approve" must be the JSON boolean true or false.')

    item = table.get_item(Key={"id": enquiry_id}).get("Item")
    if not item:
        return _resp(404, {"error": "Enquiry not found."})
    if item.get("status") != PENDING:
        return _resp(409, {"error": "This enquiry has already been decided or cannot be approved.",
                           "status": item.get("status")})

    entry = PreparedDraft(
        item["customer_message"],
        intent=item.get("intent"),
        draft_reply=item.get("draft_reply"),
        needs_escalation=bool(item.get("needs_escalation")),
    ).to_entry(approved=approve)  # StatusLogEntry validator enforces the 'sent' invariant

    try:
        table.update_item(
            Key={"id": enquiry_id},
            UpdateExpression="SET #s = :s, approved = :a, decided_at = :t, decided_by = :b",
            ConditionExpression="#s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": entry.status, ":a": entry.approved, ":t": now_iso(),
                                       ":b": caller, ":pending": PENDING},
        )
    except Exception as exc:  # noqa: BLE001
        if _is_conditional_failure(exc):
            return _resp(409, {"error": "This enquiry was decided by someone else just now."})
        raise
    return _resp(200, {"id": enquiry_id, "status": entry.status, "approved": entry.approved,
                       "draft_reply": entry.draft_reply})


def handle(event: dict, table, parser_factory: Callable[[], GeminiEnquiryParser],
           data_path: Path = DATA_PATH) -> dict:
    http = (event.get("requestContext") or {}).get("http") or {}
    method = http.get("method", "")
    parts = [p for p in (event.get("rawPath") or "/").split("/") if p]
    try:
        if method == "POST" and parts == ["enquiries"]:
            return _create(_body(event), table, parser_factory, data_path)
        if method == "POST" and len(parts) == 3 and parts[0] == "enquiries" and parts[2] == "decision":
            return _decide(parts[1], _body(event), table, _caller(event))
        if method == "GET" and len(parts) == 2 and parts[0] == "enquiries":
            item = table.get_item(Key={"id": parts[1]}).get("Item")
            return _resp(200, item) if item else _resp(404, {"error": "Enquiry not found."})
        if method == "GET" and parts == ["health"]:
            return _resp(200, {"ok": True})
    except ValueError as exc:
        return _resp(400, {"error": str(exc)})
    return _resp(404, {"error": "Not found."})

"""Offline tests for the AWS Lambda front end (AWS-01..AWS-08).

DynamoDB is an in-memory fake that honours the conditional expressions the handler relies on, and
Gemini is faked, so these run with no AWS account, no API key and no network.
"""

import base64
import json
from pathlib import Path

import pytest

from aws.handler import PENDING, handle
from src.gemini_client import GeminiEnquiryParser
from src.models import ESCALATION_MESSAGE, MAX_MESSAGE_CHARS

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "studio_data.json"


class ConditionalCheckFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    def __init__(self):
        self.items: dict[str, dict] = {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression == "attribute_not_exists(id)" and Item["id"] in self.items:
            raise ConditionalCheckFailed()
        self.items[Item["id"]] = dict(Item)

    def get_item(self, Key):
        item = self.items.get(Key["id"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues):
        item = self.items[Key["id"]]
        if item.get("status") != ExpressionAttributeValues[":pending"]:
            raise ConditionalCheckFailed()
        item.update(status=ExpressionAttributeValues[":s"], approved=ExpressionAttributeValues[":a"],
                    decided_at=ExpressionAttributeValues[":t"], decided_by=ExpressionAttributeValues[":b"])


def analysis(intent="pricing_and_availability", packages=("12 sessions",), slots=("all_saturday",)):
    return json.dumps({"intent": intent,
                       "details": {"requested_packages": list(packages), "requested_slots": list(slots),
                                   "unanswerable_topics": []},
                       "requires_human_approval": True})


def event(method, path, body=None, b64=False):
    raw = json.dumps(body) if isinstance(body, dict) else body
    if b64 and raw is not None:
        raw = base64.b64encode(raw.encode()).decode()
    return {"rawPath": path, "body": raw, "isBase64Encoded": b64,
            "requestContext": {"http": {"method": method},
                               "authorizer": {"iam": {"userArn": "arn:aws:iam::123456789012:user/staff"}}}}


@pytest.fixture
def table():
    return FakeTable()


def call(table, ev, payload=None, calls=None):
    def factory():
        if calls is not None:
            calls.append(1)
        if isinstance(payload, Exception):
            def boom(_msg):
                raise payload
            return GeminiEnquiryParser(raw_call_fn=boom)
        return GeminiEnquiryParser(raw_call_fn=lambda _msg: payload or analysis())

    resp = handle(ev, table=table, parser_factory=factory, data_path=DATA_PATH)
    return resp["statusCode"], json.loads(resp["body"])


def test_aws01_create_stores_pending_draft_from_approved_data(table):
    code, body = call(table, event("POST", "/enquiries", {"message": "12-session pack and Saturday slots?"}))
    assert code == 201 and body["status"] == PENDING
    assert "R1,680" in body["draft_reply"] and "Saturday 10:00: Full" in body["draft_reply"]
    assert table.items[body["id"]]["status"] == PENDING
    assert table.items[body["id"]]["approved"] is False


def test_aws02_approve_once_records_sent_and_blocks_a_second_decision(table):
    _, draft = call(table, event("POST", "/enquiries", {"message": "12 sessions?"}))
    code, result = call(table, event("POST", f"/enquiries/{draft['id']}/decision", {"approve": True}))
    assert code == 200 and result["status"] == "sent" and result["approved"] is True
    assert table.items[draft["id"]]["decided_by"].endswith("user/staff")
    code, again = call(table, event("POST", f"/enquiries/{draft['id']}/decision", {"approve": False}))
    assert code == 409 and table.items[draft["id"]]["status"] == "sent"


@pytest.mark.parametrize("payload, expected", [(analysis(), "rejected"),
                                               (analysis(packages=("20 sessions",), slots=()), "escalated_not_sent")])
def test_aws03_decline_never_records_sent(table, payload, expected):
    _, draft = call(table, event("POST", "/enquiries", {"message": "price?"}), payload)
    code, result = call(table, event("POST", f"/enquiries/{draft['id']}/decision", {"approve": False}))
    assert code == 200 and result["status"] == expected and result["approved"] is False
    if expected == "escalated_not_sent":
        assert draft["draft_reply"] == ESCALATION_MESSAGE


@pytest.mark.parametrize("payload, status", [(TimeoutError("slow"), "error_ai_service_failure"),
                                             ("{broken", "error_invalid_ai_output")])
def test_aws04_ai_failures_store_error_and_cannot_be_approved(table, payload, status):
    code, body = call(table, event("POST", "/enquiries", {"message": "price?"}), payload)
    assert code == 502 and body["status"] == status
    code, _ = call(table, event("POST", f"/enquiries/{body['id']}/decision", {"approve": True}))
    assert code == 409 and table.items[body["id"]]["status"] == status


@pytest.mark.parametrize("approve", ["yes", 1, None])
def test_aws05_approval_must_be_json_boolean(table, approve):
    _, draft = call(table, event("POST", "/enquiries", {"message": "12 sessions?"}))
    code, _ = call(table, event("POST", f"/enquiries/{draft['id']}/decision", {"approve": approve}))
    assert code == 400 and table.items[draft["id"]]["status"] == PENDING


@pytest.mark.parametrize("body", [{"message": ""}, {"message": "x" * (MAX_MESSAGE_CHARS + 1)},
                                  {"message": 42}, {}, "not json", "[1, 2]"])
def test_aws06_invalid_requests_never_reach_gemini_or_storage(table, body):
    calls = []
    code, _ = call(table, event("POST", "/enquiries", body), calls=calls)
    assert code == 400 and calls == [] and table.items == {}


def test_aws07_data_unavailable_is_stored_as_error(table, tmp_path):
    resp = handle(event("POST", "/enquiries", {"message": "price?"}), table=table,
                  parser_factory=lambda: pytest.fail("Gemini must not be used"), data_path=tmp_path / "missing.json")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 503 and table.items[body["id"]]["status"] == "error_data_unavailable"


def test_aws08_routes_get_base64_and_unknown(table):
    _, draft = call(table, event("POST", "/enquiries", {"message": "12 sessions?"}, b64=True))
    code, item = call(table, event("GET", f"/enquiries/{draft['id']}"))
    assert code == 200 and item["status"] == PENDING
    assert call(table, event("GET", "/enquiries/nope"))[0] == 404
    assert call(table, event("GET", "/health"))[0] == 200
    assert call(table, event("DELETE", "/enquiries"))[0] == 404
    assert call(table, event("POST", "/enquiries/nope/decision", {"approve": True}))[0] == 404

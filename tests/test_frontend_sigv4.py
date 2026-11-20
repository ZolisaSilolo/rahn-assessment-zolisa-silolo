"""FE-01..FE-02: the browser SigV4 signer (aws/frontend/sigv4.js) matches botocore exactly.

Runs the JavaScript with Node (Web Crypto API) and compares its signature with botocore's SigV4Auth
for the same request, credentials and timestamp. Skipped when Node or botocore is unavailable.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

botocore_auth = pytest.importorskip("botocore.auth")
from botocore.awsrequest import AWSRequest  # noqa: E402
from botocore.credentials import Credentials  # noqa: E402

NODE = shutil.which("node")
SIGV4_JS = (Path(__file__).resolve().parent.parent / "aws" / "frontend" / "sigv4.js").as_uri()
CREDS = {"accessKeyId": "AKIDEXAMPLE", "secretAccessKey": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
         "sessionToken": "IQoJb3JpZ2luX2VjEXAMPLETOKEN=="}
URL_BASE = "https://abc123example.lambda-url.us-east-1.on.aws"
FIXED_ISO = "2026-11-20T14:45:00.000Z"
AMZ_DATE = "20261120T144500Z"


def js_sign(method: str, url: str, body: str) -> dict:
    script = (
        f"import {{ signRequest }} from {json.dumps(SIGV4_JS)};\n"
        f"const h = await signRequest({{ method: {json.dumps(method)}, url: {json.dumps(url)}, body: {json.dumps(body)},"
        f" region: 'us-east-1', service: 'lambda', credentials: {json.dumps(CREDS)}, date: new Date({json.dumps(FIXED_ISO)}) }});\n"
        "console.log(JSON.stringify(h));\n"
    )
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def botocore_signature(method: str, url: str, body: str, payload_hash: str) -> str:
    request = AWSRequest(method=method, url=url, data=body.encode("utf-8") if body else None, headers={
        "x-amz-content-sha256": payload_hash, "x-amz-date": AMZ_DATE, "x-amz-security-token": CREDS["sessionToken"]})
    request.context["timestamp"] = AMZ_DATE
    auth = botocore_auth.SigV4Auth(Credentials(CREDS["accessKeyId"], CREDS["secretAccessKey"], CREDS["sessionToken"]),
                                   "lambda", "us-east-1")
    canonical = auth.canonical_request(request)
    return auth.signature(auth.string_to_sign(request, canonical), request)


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
@pytest.mark.parametrize("method, path, body", [
    ("POST", "/enquiries", json.dumps({"message": "How much is the 12-session pack?"})),
    ("POST", "/enquiries/939f56d7-65cc-4ae4-a724-24f6c43edde3/decision", json.dumps({"approve": True})),
    ("GET", "/health", ""),
])
def test_fe01_js_signature_matches_botocore(method, path, body):
    headers = js_sign(method, URL_BASE + path, body)
    assert headers["x-amz-date"] == AMZ_DATE
    assert headers["x-amz-security-token"] == CREDS["sessionToken"]
    js_signature = headers["authorization"].rsplit("Signature=", 1)[1]
    assert js_signature == botocore_signature(method, URL_BASE + path, body, headers["x-amz-content-sha256"])
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date;x-amz-security-token" in headers["authorization"]


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
def test_fe02_tampered_body_changes_signature():
    original = js_sign("POST", URL_BASE + "/enquiries/x/decision", json.dumps({"approve": False}))
    tampered = js_sign("POST", URL_BASE + "/enquiries/x/decision", json.dumps({"approve": True}))
    assert original["authorization"] != tampered["authorization"]

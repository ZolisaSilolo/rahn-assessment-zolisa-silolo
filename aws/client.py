"""Staff client for the deployed FitDesk API: draft -> 'Send? (y/n)' -> decision.

Signs every request with your AWS credentials (SigV4), because the Function URL uses AWS_IAM auth.
Uses the same approval prompt as the CLI.

    pip install -r aws/requirements-client.txt
    python aws/client.py --url https://<id>.lambda-url.<region>.on.aws/ "How much is the 12-session pack?"
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import botocore.session  # noqa: E402
from botocore.auth import SigV4Auth  # noqa: E402
from botocore.awsrequest import AWSRequest  # noqa: E402

from src.assistant import prompt_human_approval  # noqa: E402


def call(base_url: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    host = urlparse(base_url).hostname or ""
    region = host.split(".")[2]  # <url-id>.lambda-url.<region>.on.aws
    credentials = botocore.session.get_session().get_credentials()
    if credentials is None:
        raise SystemExit("No AWS credentials found. Run `aws login` or configure a profile first.")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = AWSRequest(method=method, url=base_url.rstrip("/") + path, data=data,
                         headers={"content-type": "application/json"})
    SigV4Auth(credentials, "lambda", region).add_auth(request)
    req = urllib.request.Request(request.url, data=data, method=method, headers=dict(request.headers))
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FitDesk staff client (AWS)")
    parser.add_argument("--url", required=True, help="FunctionUrl output of the CloudFormation stack")
    parser.add_argument("message", help="Customer enquiry text")
    args = parser.parse_args(argv)

    code, draft = call(args.url, "POST", "/enquiries", {"message": args.message})
    if code != 201:
        print(f"[{code}] {draft.get('error')} Status: {draft.get('status', '-')}. Reply was NOT sent.")
        return 1

    approved = prompt_human_approval(draft["draft_reply"], draft["needs_escalation"])
    code, result = call(args.url, "POST", f"/enquiries/{draft['id']}/decision", {"approve": approved})
    if code != 200:
        print(f"[{code}] {result.get('error')}")
        return 1
    print(f"Recorded status: {result['status']} (enquiry {draft['id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

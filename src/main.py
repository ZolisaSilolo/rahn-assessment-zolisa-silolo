"""Command-line entrypoint for the FitDesk Enquiry Assistant."""

import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

from src.assistant import (
    FitDeskAssistant,
    StatusLogError,
    append_status_log,
    now_iso,
    validate_message,
)
from src.data_store import DataUnavailableError, StudioDataStore
from src.gemini_client import GeminiEnquiryParser
from src.models import StatusLogEntry

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "studio_data.json"
DEFAULT_LOG_PATH = PROJECT_ROOT / "status_log.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FitDesk Enquiry Assistant — Hermanus Fitness Studio CLI"
    )
    parser.add_argument(
        "-m",
        "--message",
        type=str,
        default=None,
        help="Customer enquiry text. If omitted, prompts interactively.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help="Path to approved studio JSON data file.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to status log JSON file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Exit codes: 0 = enquiry processed (any status), 1 = not processed."""
    load_dotenv()
    args = build_parser().parse_args(argv)

    message = args.message
    if not message:
        try:
            message = input("Customer enquiry: ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 1

    try:
        message = validate_message(message)
    except ValueError as exc:
        print(exc)
        return 1

    try:
        # Layer 01: approved data is loaded before any AI call, so bad data never reaches Gemini
        try:
            data_store = StudioDataStore(args.data)
        except DataUnavailableError as exc:
            print(f"[Error] Studio data unavailable: {exc}")
            print("Please answer this customer manually. Reply was NOT sent.")
            append_status_log(
                StatusLogEntry(
                    timestamp=now_iso(),
                    customer_message=message,
                    status="error_data_unavailable",
                    error=str(exc),
                ),
                args.log,
            )
            return 1

        assistant = FitDeskAssistant(
            parser=GeminiEnquiryParser(),
            data_store=data_store,
            log_path=args.log,
        )
        assistant.handle_enquiry(message)
        return 0
    except StatusLogError as exc:
        print(f"[Error] {exc}. Nothing was recorded.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

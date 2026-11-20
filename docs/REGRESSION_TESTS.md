# Regression Tests: FitDesk Enquiry Assistant

Every automated test uses a fake Gemini response, so the suite runs offline with no API key and no
paid calls. Test IDs appear in the test function names, and requirement IDs refer to `docs/SRS.md`.

```bash
python -m pytest -v
```

**Last full run:** 20 Nov 2026, 14:27 SAST, Windows 11, Python 3.12, `GEMINI_API_KEY` unset.
Result: **32 passed in 1.44 s**. That is 19 test IDs; several are parametrised over more than one
input.

## Required tests (brief section 06)

| ID | Req | Input / precondition | Steps | Expected | Actual | Result |
|---|---|---|---|---|---|---|
| RT-01 Normal enquiry | FR-01–FR-05 | "How much is the 12-session pack and do you have Saturday slots?"; fake AI returns `12 sessions` + `all_saturday`; staff `y` | `handle_enquiry` | Draft has `R1,680`, `09:00: Available`, `10:00: Full`, `11:00: Available`; no escalation; `sent` | As expected | PASS |
| RT-02 Fact not in data | FR-04, FR-05, FR-06 | Fake AI requests `20 sessions`; run once with staff `n`, once with `y` | `handle_enquiry` ×2 | Draft is exactly the escalation phrase; `n` → `escalated_not_sent`, `y` → `sent` | As expected | PASS |
| RT-03 Broken or invalid JSON | FR-02, NFR-02 | Fake AI returns (a) `{broken json`, (b) JSON missing fields, (c) invalid `intent` | `handle_enquiry` with a spy on input | Prompt never shown; `error_invalid_ai_output` | All 3 rejected; input never called | PASS |
| RT-04 AI service failure | NFR-02 | Fake AI raises `TimeoutError` | `handle_enquiry` with a spy on input | Prompt never shown; `error_ai_service_failure` with an error message | As expected | PASS |
| RT-05 Staff says `n` | FR-05, FR-06 | Valid single-session draft; AI sets `requires_human_approval=false` | Staff answers `n` | Prompt still shown; draft has `R180`; `rejected`, `approved: false` | As expected | PASS |
| RT-06 Staff says `y` | FR-05, FR-06 | Valid draft for Saturday 09:00 and 10:00 | Staff answers `y` | One log record: `sent`, `approved: true` | As expected | PASS |

## Additional automated tests

| ID | Req | Input / precondition | Expected | Result |
|---|---|---|---|---|
| RT-07 | FR-04, A1 | 5 sessions + Sunday 09:00 | Whole reply escalates; `escalated_not_sent` | PASS |
| RT-08 | NFR-03 | `GEMINI_API_KEY` removed; SDK call patched | Works offline; `R800` in draft | PASS |
| RT-09 | FR-04 | `saturday_availability` with empty lists | All three slots from the data file; no false escalation | PASS |
| RT-10 | FR-02, NFR-01 | Real client, no API key | `error_ai_service_failure`; prompt never shown | PASS |
| RT-11 | NFR-02 | Fake SDK client: empty text, then a timeout | Client built with a 20 000 ms timeout; empty → invalid output; timeout → service error | PASS |
| RT-12 | FR-06, NFR-02 | Truncated `status_log.json` exists | Old log moved to `status_log.corrupt-*.json` unchanged; new log holds 1 record | PASS |
| RT-13 | FR-03, NFR-02 | Data file missing, `{oops`, or no packages (via `main`) | Exit code 1; `error_data_unavailable` logged; Gemini never constructed | PASS (3 cases) |
| RT-14 | FR-01, NFR-03 | Empty, whitespace-only, or 1 001-character message | Exit code 1; nothing logged; Gemini never constructed | PASS (3 cases) |
| RT-15 | FR-06 | Build `StatusLogEntry` objects with an unapproved or draftless `sent` | Pydantic `ValidationError` | PASS |
| RT-16 | FR-05 | Answers `YES`; `No`; `maybe`/`?`/`ok`; EOF | `sent`; `rejected`; `rejected` after 3 prompts; `rejected` | PASS (4 cases) |
| RT-17 | FR-04 | Fake AI returns bare `09:00` | Escalates; Saturday is not assumed | PASS |
| RT-18 | Rule 1, NFR-01 | 5 analyses: all packages, mixed, one slot, unknown package, out of scope | Every `R` amount and `HH:MM` in each draft exists in the data file | PASS (5 cases) |
| RT-19 | FR-03 | Data file with price `180 rand`, or status `Maybe` | `DataUnavailableError` at load | PASS |

## Manual runs of the real CLI (no mocks)

| ID | Command | Expected | Actual (20 Nov 2026, 14:25) | Result |
|---|---|---|---|---|
| M-01 | `python -m src.main --message "How much is the 12-session pack and do you have Saturday slots?"` with no `.env` | AI-unavailable message, no prompt, `error_ai_service_failure`, exit 0 | As expected | PASS |
| M-02 | `--data nope.json` | Data-unavailable message, exit 1, `error_data_unavailable` | As expected | PASS |
| M-03 | 1 001-character message | Limit message, exit 1, nothing logged | As expected | PASS |
| M-04 | Empty interactive input | `No customer enquiry provided.`, exit 1 | As expected | PASS |
| M-05 | Live Gemini run of the RT-01 message with a real key in `.env` | Valid JSON; the draft from RT-01; `sent` after `y` | **Not run yet** (needs the candidate's key) | PENDING |

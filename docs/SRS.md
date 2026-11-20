# Mini-SRS: FitDesk Enquiry Assistant (CLI MVP)

RAHN Agentic AI Internship, January 2027 assessment · v1.1 · 20 November 2026

## 1. Business objective
A Hermanus fitness studio answers repetitive enquiries about session packages and Saturday slots by
hand. The owner wants a command-line assistant that drafts accurate replies from approved data only,
never invents a price or slot, and records nothing as sent without staff approval.

## 2. Stakeholders
- **Studio owner:** owns the price list and availability file; needs accurate replies.
- **Staff on duty:** run the CLI and approve (`y`) or decline (`n`) each draft.
- **Clients:** receive truthful facts or an honest "I'll check" reply.

## 3. Scope
- **In:** one enquiry per run at the CLI; Gemini parsing to Pydantic-validated JSON; lookup in
  `data/studio_data.json`; a reply composed in code; the `Send? (y/n)` gate; `status_log.json`.
- **Out:** WhatsApp, bookings, calendar, payments, editing drafts, multi-turn chat.

## 4. Functional requirements
- **FR-01 Input:** accept an enquiry via `--message` or a prompt. Reject empty input or more than
  1,000 characters before any AI call.
- **FR-02 Structured parsing:** send the enquiry to Gemini once and require JSON containing `intent`,
  `details` and `requires_human_approval`, validated by the `EnquiryAnalysis` Pydantic model.
- **FR-03 Approved data:** load and validate `data/studio_data.json` (prices like `R1,680`, statuses
  `Available` or `Full`) before calling the AI. Prices and slots come from this file only.
- **FR-04 Lookup:** map requested packages and slots to approved entries through a fixed alias list.
  Anything unmatched, any other day or a bare time, and any out-of-scope topic is a missing fact.
- **FR-05 Draft and approval gate:** compose the draft in code. If any fact is missing, the draft is
  exactly `I'll check with the owner and come back to you.` Print it and ask `Send? (y/n)`.
- **FR-06 Status log:** append one record per processed enquiry to `status_log.json`. `sent` is
  recorded only for an existing draft that staff approved.

## 5. Non-functional requirements
- **NFR-01 Security:** `GEMINI_API_KEY` lives only in a git-ignored `.env`; `.env.example` is
  committed. The LLM never sees or writes prices, so an instruction hidden in an enquiry cannot
  change a fact.
- **NFR-02 Reliability:** AI errors, a 20-second timeout, invalid JSON, bad data and log corruption
  all end in a clear message and a non-`sent` outcome, never a traceback. Log writes are atomic,
  and a corrupt log is backed up rather than overwritten.
- **NFR-03 Cost and testability:** free Gemini tier, at most one call per enquiry, no retries. The
  full test suite runs offline with no API key in under 5 seconds.

## 6. Data inputs and outputs
- **Inputs:** enquiry text; `data/studio_data.json` (Single session R180, 5 sessions R800,
  12 sessions R1,680; Saturday 09:00 Available, 10:00 Full, 11:00 Available); the staff answer;
  `GEMINI_API_KEY` and `GEMINI_MODEL` from `.env`.
- **Outputs:** a printed draft and prompt. Each `status_log.json` record holds: `timestamp` (UTC),
  `customer_message`, `intent`, `draft_reply`, `needs_escalation`, `approved`, `status`, `error`.

## 7. Approval and failure rules
| Situation | Approval asked? | Logged status |
|---|---|---|
| Staff `y`/`yes` | yes | `sent` (including an approved escalation) |
| Staff `n`/`no`, Ctrl+D, or 3 invalid answers | yes | `rejected` or `escalated_not_sent` |
| Gemini error, timeout or missing key | no | `error_ai_service_failure` |
| Broken or schema-invalid JSON | no | `error_invalid_ai_output` |
| Data file missing or invalid | no (Gemini not called) | `error_data_unavailable` |
| Empty or oversized message | no | not logged; exit code 1 |

## 8. Measurable acceptance criteria
- **AC-01:** "12-session pack and Saturday slots?" gives a draft containing `R1,680` and all three
  Saturday statuses, with no escalation.
- **AC-02:** a 20-session pack or a Sunday slot gives a draft exactly equal to the escalation phrase.
- **AC-03:** broken or invalid JSON never shows the prompt and logs `error_invalid_ai_output`.
- **AC-04:** an AI exception or timeout never shows the prompt and logs `error_ai_service_failure`.
- **AC-05 / AC-06:** staff `n` logs `rejected` with `approved: false`; staff `y` logs `sent` with
  `approved: true`.
- **AC-07:** every `R` amount and `HH:MM` time in any draft exists in the data file.

## 9. Ambiguities and assumptions
| # | Unclear in the brief | Question for the owner | Assumption |
|---|---|---|---|
| 1 | Mixed enquiries (one known and one unknown fact) | Answer the known part, or escalate it all? | Escalate the whole reply with the exact phrase; no partial answers. |
| 2 | Booking requests ("Can I book 09:00?") | State availability, or escalate? | Bookings are out of scope, so escalate. |
| 3 | Which Saturday the file covers | Is it always this coming Saturday? | Yes. Replies say "Saturday" with no date, because no date is in the data. |
| 4 | Meaning of "sent" | Should the tool transmit messages? | No. `sent` means approved; staff paste the reply into WhatsApp. |
| 5 | Storing customer messages (POPIA) | May messages be kept? | Only in the local, git-ignored log. |

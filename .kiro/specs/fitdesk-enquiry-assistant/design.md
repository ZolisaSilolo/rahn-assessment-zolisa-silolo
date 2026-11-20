# Design: FitDesk Enquiry Assistant (as built)

The architecture diagrams (Mermaid and plain text) and the layer map are in `README.md`, under
"Architecture".

## Key decision: the LLM classifies, the code writes the reply

Gemini returns only `EnquiryAnalysis` (intent plus the package and slot names asked about). Python
maps those names to approved entries through a fixed alias list and composes the reply. Rule 1 then
holds by construction, and a prompt injection cannot change a fact. The trade-off is that replies
read like templates, and any unknown fact escalates the whole reply (SRS assumption 1).

## Modules

| Module | Responsibility | Key interface |
|---|---|---|
| `src/models.py` | Pydantic contracts and safety invariants | `EnquiryAnalysis`, `StudioData`, `StatusLogEntry` (validator: `sent` ⇔ approved draft), `ESCALATION_MESSAGE`, `MAX_MESSAGE_CHARS` |
| `src/data_store.py` | Load and validate approved data; deterministic lookup | `StudioDataStore(path)` raises `DataUnavailableError`; `.lookup(analysis) -> LookupResult` |
| `src/gemini_client.py` | The only network code | `GeminiEnquiryParser(api_key, model, raw_call_fn)`; `.parse_enquiry(msg)` raises `AIServiceError` or `InvalidAIOutputError`; 20 s timeout |
| `src/assistant.py` | Pipeline, approval gate, log | `validate_message`, `draft_reply`, `prompt_human_approval`, `append_status_log` (atomic, backs up a corrupt log); `FitDeskAssistant.prepare_draft(msg) -> PreparedDraft`; `PreparedDraft.to_entry(approved)`; `handle_enquiry(msg)` |
| `src/main.py` | CLI wiring and exit codes | `main(argv) -> int` |

`prepare_draft` and `to_entry` separate "produce a draft" from "record a human decision". The CLI
calls them back to back. Another front end, such as a web API with an approval endpoint, can reuse
both without changing the safety logic.

## Error handling

The full table is in `docs/SRS.md` section 7. No expected failure produces a traceback, and none
records `sent`.

## Testing

`tests/test_assistant.py` covers RT-01..RT-09, and `tests/test_hardening.py` covers RT-10..RT-19,
32 cases in total. Gemini is injected (`raw_call_fn`) or its SDK client is faked. Results are in
`docs/REGRESSION_TESTS.md`.

## Development guardrails (`.kiro/`)

| Component | What it does |
|---|---|
| `pre-tool-guard` (PreToolUse) | Enforces the task file scope; blocks `.env` reads and writes and key-shaped content; locks the README Working style section; blocks git operations that would include `.env`; asks before push, `gh` or web calls; locks writes after an escalation |
| `loop_guard.py` | Runs the tests; repeated identical failure, or 5 failed runs, means ESCALATE; only the human can reset |
| `session-start-status` | Prints the deadline, loop state, task scope and `.env` safety at session start |
| `task-start-raplh-protocol`, `task-end-raplh-evidence`, `python-change-run-loop-guard` | Inject the loop rules and the RAPLH evidence requirements |
| `guard_selftest.py` | 24 simulated tool calls through the guard |

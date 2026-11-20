# Requirements: FitDesk Enquiry Assistant (as built)

The graded requirements document is `docs/SRS.md`. It is the single source of truth, so its content
is not duplicated here:

#[[file:docs/SRS.md]]

## Core invariants (EARS), each enforced in code and covered by tests

1. THE Assistant SHALL take every price and slot status in a reply from `data/studio_data.json`
   only. (Rule 1; `draft_reply`, `StudioData`; RT-01, RT-18)
2. IF any requested fact is missing THEN the draft SHALL equal exactly
   `I'll check with the owner and come back to you.` (FR-05; RT-02, RT-07, RT-17)
3. THE Assistant SHALL record `sent` only WHEN a draft exists AND staff answered `y`/`yes`.
   (Rule 2; `StatusLogEntry` validator; RT-05, RT-06, RT-15, RT-16)
4. IF the AI fails, times out (20 s), or returns invalid JSON THEN THE Assistant SHALL NOT show the
   approval prompt. (NFR-02; RT-03, RT-04, RT-10, RT-11)
5. IF the data file is missing or invalid THEN THE Assistant SHALL NOT call Gemini. (FR-03; RT-13, RT-19)
6. THE test suite SHALL run with no API key and no network. (NFR-03; all tests)

## Brief constraints

- Free tools only: Python, GitHub, a free Gemini key.
- The tests run without a key.
- `.env` is never committed.
- The repo is public, named `rahn-assessment-firstname-lastname` in lowercase.
- The Working style answers are written without AI.

# Tasks: FitDesk Enquiry Assistant

Deadline: **Friday 20 November 2026, 17:00 SAST.** Tasks marked [HUMAN] are the candidate's.

## Done

- [x] 1. Requirements and plan from both PDFs; guardrail hooks (Kiro, RAPLH iteration 3)
- [x] 2. First build: `src/`, RT-01..RT-09, docs (Antigravity, RAPLH iterations 1–2)
- [x] 3. Gap analysis with reproduced defects (Kiro, iteration 4)
- [x] 4. Hardening: timeout, log backup and atomic write, data validation, input limit, `sent`
      invariant, yes/no and 3-attempt cap, bare-time aliases removed; RT-10..RT-19 (32 passing)
- [x] 5. Cleanup: removed the outdated AWS doc and caches; `.gitignore` covers Kiro runtime state
- [x] 6. Docs: SRS trimmed to about 900 words; REGRESSION_TESTS with actual results; README with
      architecture, AI use and limitations; RAPLH iterations 3–5 and evidence-based Q1/Q2

## Remaining for submission

- [ ] 7. [HUMAN] Confirm the correct repository name (`rahn-assessment-firstname-lastname`, lowercase)
- [ ] 8. [HUMAN] Create `.env` from `.env.example`; run the live check M-05; tell the agent the
      result so the docs can be updated
- [ ] 9. [HUMAN, NO AI] Write the four Working style answers (80–150 words each); fill in `Hours spent`
- [ ] 10. [HUMAN] Review the RAPLH entries and replace each *PENDING candidate review* with your decision
- [ ] 11. `git init`, first commit, push to a public repo (human approves the push); fresh-clone test
- [ ] 12. [HUMAN] Email the repo link to sybil@rahn.co.za, subject exactly
      `RAHN Jan 2027 Assessment — Firstname Lastname`, by 17:00

## Phase 2 (after submission is safe): run on AWS as a real application

- [ ] 13. Confirm the AWS project, selected Region, plan (free or paid) and spend limit
- [ ] 14. Agree the design: API front end (`prepare_draft`) + approval endpoint (`to_entry`) +
      DynamoDB log + the Gemini key in SSM Parameter Store, all in the selected Region.
      Keep it on a separate branch so the graded `main` stays within the brief's free-tools rule.
- [ ] 15. Build with IaC; deploy only after explicit approval; smoke test; decide on cleanup

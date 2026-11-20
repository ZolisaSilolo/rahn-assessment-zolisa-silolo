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

- [x] 7. Repository name `rahn-assessment-zolisa-silolo` (public)
- [x] 8. Live check: done on Amazon Bedrock (L-01..L-03); the live Gemini run was not done (M-05)
- [ ] 9. [HUMAN, NO AI] Working style answers. Not written by the deadline; stated honestly in the README.
- [x] 10. RAPLH entries approved by the candidate
- [x] 11. Pushed, fresh clone passed 41/41
- [x] 12. Submission emailed 20 Nov 2026, 16:40 SAST (before the 17:00 deadline). `main` frozen since.

## Phase 2: run on AWS as a real application (branch `aws-deploy`)

- [x] 13. Region `us-east-1` chosen by the candidate (the account already runs workloads there)
- [x] 14. Staff API: Lambda function URL (IAM auth) + DynamoDB + Bedrock Nova Micro; checks S-1..S-8
- [x] 15. Staff web app: private S3 + CloudFront OAC + CSP, Cognito managed login (PKCE), identity
      pool + browser SigV4; checks W-1..W-10 and E2E-1..E2E-4 in a real browser
- [x] 16. Owner staff account invited by email; the stack is kept running
- [ ] 17. Push `aws-deploy` to GitHub after marking (23–27 Nov), as agreed with the candidate

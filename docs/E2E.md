# End-to-end verification

The Python suite and browser checks have different scopes. Neither synthetic
fixtures nor a passing test count establishes the accuracy of collected menus.

## Repeatable application tests

```bash
python -m pytest tests -q
```

`test_workflow_completion.py` uses the actual FastAPI application, SQLite,
LangGraph and deterministic ranker, with a reviewed **fictional** catalog. It
tests simultaneous private answers, stale edits, private vetoes and delegation,
selection/reconfirmation, deadlines, manual unverified plans, and actual-visit
feedback and reversible learning. Time is controlled at the application clock
boundary for deadlines and post-meal feedback. The primary successful journeys use the real ranker. One deliberate delayed
callback isolates the selection/revocation race; it is not live-provider evidence.

`test_api.py` retains focused state/authorization regression tests with a stub
recommendation callback. `test_account_email.py` and `test_email_app_flow.py` use
a fake external mail transport, not live SMTP.

## What the inference checks establish

- `test_inference.py` and the model cases in `test_agent.py` use controlled fake
  provider responses to test configuration, approved-label validation, failure
  handling, usage metadata and preservation of the rule-based ranking. They do
  not run a real language model.
- `test_inference_protocol.py` uses the real chat client and HTTP adapter against
  a local fixture server with a synthetic key. It checks the request path,
  authentication header, bounded payload, returned usage and HTTP-error fallback.
  Its response is predetermined; it is not an ILMU service or inference test.
- The browser suite explicitly disables inference, even if the developer shell
  has model credentials. It checks the actual graph/ranker and user journey, plus
  the distinction between configured settings and recorded result metadata. It
  asserts both the home label `Rule-based recommendations` and the result label
  `No LLM used`, backed by a persisted zero-call, disabled result.
- Live ILMU inference is a separate check using an explicitly selected model and
  locally configured credential. Passing the suites above does not establish
  credential validity, live provider availability, model-output quality or
  recommendation usefulness to real diners. See [INFERENCE.md](INFERENCE.md) for
  configuration and live-check instructions.

Food requirement checks and ranking remain Python rules. A separate optional flow
can turn explicitly approved craving text into an editable structured draft. The
browser contract verifies consent, review, manual deletion, ordinary check-in save,
late-response rejection and fallback, but stubs that remote boundary. It does not
train a taste-prediction model.

## Browser journey

Start a **separate** local server with an empty disposable database:

```bash
DINING_LLM_PROVIDER=disabled python webapp.py --demo --db /tmp/makan-browser-test.sqlite3 --port 7863
python scripts/seed_browser_e2e.py --base-url http://127.0.0.1:7863
```

The seed refuses non-loopback and non-demo targets. It creates nine fictional
accounts and one room through the real HTTP API. Run once per empty test DB.
The fixed password printed by this tool belongs only to these test accounts.
The demo catalog must have current fictional evidence and valid hours for the
chosen test meal. Do not modify real restaurant facts to make a test pass.

`scripts/browser_e2e.js` contains `runBrowserE2E(ownerTab, friendTab, config)` for
the supported CUA browser runtime. Open one tab at `http://127.0.0.1:7863` and one
at `http://localhost:7863` so their host-scoped cookies represent separate users.
Pass `ownerEmail: 'e2e1@example.test'`, `friendEmail: 'e2e2@example.test'`, the
printed test password, and `port: 7863`. The script uses normal UI controls:

1. Sign in and open the nine-member room.
2. Invite two people, rather than every room member.
3. Save separate private check-ins.
4. Generate a shortlist using the actual graph and ranker.
5. Exercise data-error reporting during shortlist review.
6. Approve as one person, veto as the other, and check that the veto blocks choice
   without revealing its private reason.
7. Change that person's response explicitly and confirm the unanimous choice.

These browser steps were exercised in the Codex in-app browser on 5 October 2026.
Additional browser checks passed for sign-in preserving a meal link, two-tab
conflict detection with the draft retained, reconfirmation after a profile change,
the complete two-person manual-plan acknowledgement path, and the restricted
operations form. A 320px Malay view had no horizontal overflow; this checks layout,
not translation quality or WCAG conformance. Future-meal feedback is hidden until
planned finish. Backend time-controlled tests cover actual post-visit feedback.
The CUA harness remains available for manual inspection. The standalone browser
suite below now covers unattended regressions; actual-device/screen-reader release
checks remain separate.

## Deliberately unverified

Live SMTP delivery, browser push, actual iOS/Android devices, screen readers,
load/failover, disaster recovery, real restaurant suitability and production
privacy/provider approvals require separate evidence. See `PRD_ACCEPTANCE.md`.

## Standalone browser suite

```bash
python -m pip install -r requirements-test.txt
npm ci
npx playwright install chromium webkit
npm run test:e2e
# One project while developing:
npm run test:e2e:chromium
```

Use Node 22+ and Python 3.12. `DINING_TEST_PYTHON` can select a Python executable;
otherwise `.venv/bin/python` is used when present, then `python` on PATH.
`DINING_E2E_PORT` defaults to 7879. The runner refuses to reuse a server already
listening on that port. No running app database is opened or modified.

`tests/browser/server.py` creates a temporary database and an explicitly fictional,
current catalog, explicitly forces inference off and disables live mail/provider
tracing, starts the actual FastAPI app
and seeds a separate nine-diner group for each browser project through HTTP, so
projects do not exhaust one shared account’s sign-in quota. The worker, graph and ranker are real. Each
person uses an isolated browser context; no restaurant API or recommendation is
mocked. The network-race case delays a real prior response; the failed-send case
uses browser offline mode. Browser clock control advances the polling timer only.

Eight scenarios run in desktop Chromium, 320px Chromium and iPhone-sized WebKit:

1. A nine-member room invites two diners; they check in, queue generation, reload,
   verify the zero-model-call result and its honest UI label, exercise a private
   veto, and unanimously select a revalidated option.
2. A private draft survives in-tab navigation and a failed send; a stale second tab
   cannot overwrite the saved answer.
3. Keyboard dialogs restore focus, and logout/account changes clear private drafts.
4. A delayed inbox poll from the signed-out user cannot replace the new user's inbox.
5. AI drafting requires consent, stays provisional, preserves the firm cap and saves
   only through a reviewed ordinary check-in.
6. A manual edit invalidates a pending model result before it can alter the form.
7. Unavailable inference leaves the ordinary check-in usable and later input clears
   an old proposal.
8. One server-issued M09 question exposes only generic cuisine choices, remains
   optional, and saves an explicit owner choice through the authenticated endpoint.

The suite runs axe WCAG A/AA checks on auth/profile/check-in/shortlist/dialog views
and checks horizontal reflow. This is automated accessibility evidence, **not** a
WCAG certification, manual screen-reader check, or test on a physical iPhone/Android.
Private drafts intentionally do not survive refresh or sign-out. Submitted answers
and recommendation jobs do survive reconnects.

Failures save local screenshots/traces/HTML reports under ignored test directories.
Only fictional test accounts belong in these artifacts. `.github/workflows/web-pilot.yml`
adds backend and browser checks for CI; a remote CI run is not claimed until pushed
and actually observed. Setup follows Playwright's [managed test server](https://playwright.dev/docs/test-webserver),
[isolated contexts](https://playwright.dev/docs/browser-contexts) and
[accessibility testing](https://playwright.dev/docs/accessibility-testing) guides.

Recorded local result after adding inference assertions: **12 / 12 passed**
(32.2 seconds), covering four scenarios across desktop Chromium, 320px Chromium
and mobile WebKit emulation. The run included persisted zero-call metadata,
honest model-use labels, axe checks and reflow assertions. It used the actual
graph/ranker with inference disabled. This is not live ILMU verification, a
physical-device test or manual screen-reader sign-off.

Recorded 6 October 2026 result after the post-meal lifecycle flow: **270 / 270 backend
tests** and **24 / 24 browser cases** (eight in each configured project). The two
live synthetic ILMU probes are documented separately in `INFERENCE.md`; the
browser suite did not send diner text to ILMU.

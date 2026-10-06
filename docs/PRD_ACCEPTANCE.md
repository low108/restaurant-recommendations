# PRD acceptance and verification record

Reviewed on 5 October 2026 against the original **Group Dining PRD**, at
`/Users/johnathanjohnathan/Documents/restaurant-recommendations-prd/PRD.md`.
The original requirements have not been changed to match the implementation.

**The local app implements a substantial dining workflow. It does not yet meet
every PRD requirement or external-pilot release gate.** The tables below distinguish
implemented behavior from missing functionality and from evidence still needed.
They do not assign a completion percentage.

## How to read the evidence

- **Automated coverage** means the named test exercises the stated behavior. It
  does not mean every subclause of a requirement is covered, and is not a claim
  that an external service was contacted.
- **Actual-app integration** uses FastAPI HTTP routes, SQLite and the real dining
  LangGraph/ranker through `create_app`, with fictional catalog data. It runs in
  Python `TestClient`, rather than a browser or deployed production environment.
- **Stubbed integration** uses real account/room/meal routes and SQLite but supplies
  a fixed recommendation callback. It tests authorization and workflow behavior,
  not restaurant retrieval or ranking quality.
- **Browser checked** means the specific CUA journey described below was executed
  against a local server. It is not a full browser/device/accessibility matrix.
- **Partial / missing evidence** identifies a requirement that cannot honestly be
  marked fully accepted from the current implementation and checks.

The final test total should come from the current run output, rather than an old
number copied into this document. Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check dining tests webapp.py scripts
.venv/bin/python -m ruff format --check dining tests webapp.py scripts
node --check web/app.js
node --check web/i18n.js
node --check scripts/browser_e2e.js
```

JavaScript syntax checks do not execute the UI. A passing Python suite does not
establish live restaurant accuracy, SMTP delivery, push delivery, accessibility
conformance or safe production operations.

## Test boundaries

| Evidence | What it actually exercises | Limits |
|---|---|---|
| [Workflow integration](../tests/test_workflow_completion.py) | Actual app, independent answers, stale edits, actual graph/ranking, three-state responses, delegation, selection, reconfirmation, feedback, learning review/deletion and manual plans | Fictional catalog; some cases inject time; the revocation race uses a deliberately delayed recommendation stub. No real restaurant provider or browser. |
| [API integration](../tests/test_api.py) | Sessions, CSRF, room boundaries, readiness, membership changes, stale publication, decisions, deletion and restart recovery | Default fixture uses the explicit `recommendation` stub. Passing these tests alone does not validate the ranker. |
| [Recommendation tests](../tests/test_recommendation.py) and [scoring contract](../tests/test_ranking_contract.py) | Hard gates, menu duplication perturbations, portions/prices, conservative unknowns, feature transforms, PRD formula, fit floor, diversity and venue affinity | Offline fictional records. They do not measure real-world recommendation quality or eliminate every form of dataset bias. |
| [Catalog contract](../tests/test_catalog.py), [audit](../tests/test_catalog_audit.py), [status](../tests/test_catalog_status.py) | Schema, references, unknown defaults, review flags, source gates and aggregate-only coverage | Structural validation and bounded text conflict checks do not verify facts or legal permissions. |
| [Blocked-import integration](../tests/test_real_catalog_flow.py) | Actual app/agent rejects an unapproved import without exposing its restaurant content | The test constructs the pending import from a fictional fixture. Its filename does not mean it verifies the user's real restaurant facts. |
| [Agent tests](../tests/test_agent.py) and [observability tests](../tests/test_observability.py) | Minimal model input, unsupported-output fallback, bounded calls, structural receipts, missing usage and restricted operations access | Model behavior is tested with fake providers. Native trace export remains disabled; a live Ollama/LangSmith integration is not proved by these tests. |
| [Notification tests](../tests/test_notifications.py) | Persisted inbox jobs, deduplication, opt-in, mute/expiry suppression and feedback timing | No push transport or device delivery. Service reconstruction is tested, not every process/network crash sequence. |
| [Account-email tests](../tests/test_account_email.py) and [app integration](../tests/test_email_app_flow.py) | Token expiry/single-use, email binding, recovery, session revocation, throttling, queue retry, verification gating and TLS call order | Fake mailboxes/fake SMTP only. No external email was delivered during this work. |
| [Routing tests](../tests/test_routing.py) | Provider-neutral interface, disabled/unknown defaults, deterministic fake, timeout, failure handling and shared privacy projection | Fake provider only. No external routing provider is contacted; straight-line distance is not treated as route evidence. |
| [Meal origin tests](../tests/test_meal_origin.py) | Owner-scoped origins, precise coordinates, approximate area, do-not-use mode, explicit route consent, response revision increments, invalidation, export isolation and account deletion | Isolated local test database. Real GPS sensor permission is outside these unit tests. |
| [Route eligibility tests](../tests/test_route_evidence_eligibility.py) | Deterministic route arrival gating, kitchen/must-leave cutoffs, neutral unknown handling, stale rejection, private ETA projections, and group-safe travel aggregates | Fake routing provider only. No live external routing service contacted. |
| [Accessibility tests](../tests/test_accessibility_gates.py) | Deterministic accessibility gates, evidence review/freshness, unknown handling, inaccessible blocking, withheld/unknown requirements, and private projection | Offline catalog fixtures. No physical premises inspection. |
| [Preparation confirmation tests](../tests/test_preparation_confirmation.py) | Session-specific preparation confirmations (`preparation_confirmations`), exact bounded claims, expiry/staleness handling, menu-alone allergen rejection, and learning isolation | Offline test fixtures. No real restaurant phone or staff contact. |
| [Authorization matrix tests](../tests/test_authorization_matrix.py) | Full HTTP API route matrix covering unauthenticated, non-member, uninvited member, invited participant, organizer, owner, removed member and expired session states, plus private projection verification | Isolated test SQLite database. |
| [Adversarial injection tests](../tests/test_adversarial_injection.py) | Hostile restaurant names, menu descriptions, evidence, reviews, sentinel leak attempts, tool output bounds, and safe fallback without retry amplification | Simulated adversarial fixtures. |
| [Geocoding tests](../tests/test_geocoding.py) | Geocoding contract, disabled/unknown defaults, deterministic fake, neighbourhood lookup, reverse map-pin lookup, and graceful provider failure during meal creation | Fake geocoding provider only. No external geocoding service contacted. |

## Requirement matrix

| PRD ID | Current implementation | Acceptance status and remaining gap | Evidence |
|---|---|---|---|
| **REQ-01 — Account/onboarding** | Maintained password hashing, server sessions, rate limits, adult/terms choices, private requirement review and separate learning/sensitive-data settings. Optional email verification and password reset with hashed single-use tokens and persisted delivery jobs. | **Partial.** Real SMTP delivery/configuration is unverified. Verified-email enforcement is optional and off without complete configuration. Consent is not yet a complete versioned legal-notice/processor system. | [Account API](../dining/api.py), [email service](../dining/account_email.py), API registration/auth tests, account-email and email-app tests. |
| **REQ-02 — Rooms/membership** | Expiring/revocable invitation tokens, minimal pre-join room preview without member identity or history leakage, explicit join, owner transfer, member removal/leave, archive and room reminder settings. Membership changes invalidate affected planning state. | **Core local behavior and pre-join preview covered.** Minimal pre-join preview endpoint (`GET /rooms/preview`) and UI flow implemented; complete every-route access-revocation matrix covered in test suite. | [API](../dining/api.py), [pre-join preview test](../tests/test_api.py), [authorization matrix](../tests/test_authorization_matrix.py), API isolation/invite/revocation tests, [notification settings](../dining/notifications.py). |
| **REQ-03 — Start a meal** | Meal period/date/duration, deadlines, selected invitees, shared area/coordinates, schedule-conflict warnings (`schedule_conflicts`), owner-scoped private meal origins (`meal_origins`), neighbourhood and map-pin lookup contract (`dining/geocoding.py`), and idempotent creation. Initial deadlines and invitation count are retained. | **Core local behavior, schedule-conflict warning, private origins, and geocoding contract covered.** Overlapping session warnings preserve cross-table privacy; disabled geocoding provider default preserves manual coordinate entry and failure resilience. | API meal-creation test; workflow logistics/original-deadline test; [routing contract](../dining/routing.py); [geocoding contract](../dining/geocoding.py); [test_geocoding.py](../tests/test_geocoding.py); [test_routing.py](../tests/test_routing.py); [test_meal_origin.py](../tests/test_meal_origin.py); [full lifecycle journey test](../tests/test_full_e2e_journey.py). |
| **REQ-04 — Individual check-in** | Join/decline/pending, private answers, explicit requirement confirmation, owner-scoped meal origins with explicit route consent (`meal_origins`), optional `must_leave_by` time constraints and response compare-and-swap. Optional taste, soft target and comfort inputs. A consented AI helper can draft allowlisted taste fields. M09 performs a bounded deterministic preliminary comparison and offers at most one owner-scoped question only when its choices can reorder viable options. Deterministic route evidence arrival checks apply when firm route evidence exists. | **Core local behavior, M09, private meal origins, and deterministic route arrival checks covered.** Requirement/private-text routing makes zero model calls; stale AI drafts and stale M09 opportunities are rejected. Impossible arrivals block candidate outlets; missing/stale routing remains unknown. | Workflow CAS tests; preference and adaptive-question API/ranking tests; [test_meal_origin.py](../tests/test_meal_origin.py); [test_route_evidence_eligibility.py](../tests/test_route_evidence_eligibility.py); three-project browser consent/apply/edit/save/skip/failure cases; [UI](../web/app.js). |
| **REQ-05 — Freeze/generate** | At least two ready diners, explicit pending exclusion, context/evidence/policy-keyed durable jobs, bounded attempts, lease/restart recovery, sanitized processing history and transactional publication. | **Local worker behavior covered.** No PostgreSQL/production failover. A provider ignoring its timeout can delay the worker until it returns or is restarted; this is visible in the UI. | [Generation jobs](../dining/generation.py), [job tests](../tests/test_generation_jobs.py), standalone browser reload journey. |
| **REQ-06 — Shortlist/choice** | Up to three checked options, private Works/Prefer another/Cannot eat responses, scoped revocable delegation, unanimous acceptance, final revalidation, PRD tie-breaking (`choice_mode='tie_break'`) by fit score/distance/evidence coverage/venue ID, agreed random draw (`choice_mode='random_draw'`) across unanimously accepted candidates, group-safe route aggregates, and reconfirmation after material changes. | **Core local behavior, PRD tie-breaks, agreed random draw, and group-safe route aggregates covered.** Private route ETAs owner-scoped; shared cards expose group-safe aggregates only. | Workflow vote/delegation/reconfirmation tests; [tie-break and random draw tests](../tests/test_api.py); [test_route_evidence_eligibility.py](../tests/test_route_evidence_eligibility.py); browser private-veto/selection journey; [ranker](../dining/recommendation.py). |
| **REQ-07 — After meal** | Visit/outcome, enjoyment, repeat intent, reasons, cost, dish text and fairness feedback. Confirmed alternate-branch linkage (`outcome='somewhere_else'`, `alternate_outlet_name`), separate dietary/data-error investigation reporting (`POST /meals/{meal_id}/report-data-error`), and session-specific preparation confirmations (`preparation_confirmations`). One inbox prompt after planned finish plus one hour; reversible learning review. | **Core local feedback, data-error investigation, and session-specific preparation confirmation covered.** Manual/unverified visit feedback is conservatively kept out of learning; preparation confirmation claims never enter taste learning. | Workflow rich-feedback tests; [dietary data-error report tests](../tests/test_api.py); [test_preparation_confirmation.py](../tests/test_preparation_confirmation.py); notification feedback-timing test; [API](../dining/api.py). |
| **REQ-08 — Lifecycle/revisions** | Personal/shared version checks, overdue/expired state, original deadlines, reconfirmation, cancellation, manual coordination, immutable recommendation/evidence payload archives (`recommendation_archives` surviving `meals.result` invalidation), and durable generation attempts. Confirmed plans persist `selected`/`manual_selected` → `awaiting_feedback` at planned finish → `closed` after seven days through both API access and the notification worker, without deleting decision, shortlist or feedback history. | **Core local lifecycle and immutable recommendation payload archives covered.** Processing receipts and immutable historical shortlist/evidence payloads persist independently from meal invalidation. | Workflow and post-meal lifecycle tests; job evidence-replacement/stale-vote/restart tests; [recommendation archive survival test](../tests/test_api.py); [lifecycle rules](../dining/lifecycle.py), [store](../dining/store.py). |
| **REQ-09 — Restaurant catalog** | Schema v2, v1 compatibility, identities, evidence/rights/expiry, price units, serving counts, review status, accessibility evidence structure (`AccessibilityEvidence`), upgrade/audit tooling and public coverage counts. | **Blocked for external-pilot acceptance.** Current real collection remains held for review. It does not demonstrate 50 audited useful local branches, approved source use or adequate scenarios. No kitchen-service provider. | [Catalog](../dining/catalog.py), [audit](../dining/catalog_audit.py), [import guide](CATALOG_V2.md), contract/status/blocked-import tests, [test_accessibility_gates.py](../tests/test_accessibility_gates.py). |
| **REQ-10 — Hard checks** | Deterministic source/review/price/diet/certification gates plus date-scoped holiday overrides, kitchen last orders, arrival and planned finish with overnight handling, deterministic route arrival checks, accessibility evidence gates, and session-specific preparation confirmation. | **Deterministic route, accessibility, and preparation confirmation gates covered.** Missing dated hours/kitchen evidence blocks checked publication; deterministic route, accessibility, and session-specific preparation confirmations apply when firm evidence exists. | Recommendation gates, dedicated A08 fixture, [hours acceptance tests](../tests/test_hours_exceptions.py), [test_route_evidence_eligibility.py](../tests/test_route_evidence_eligibility.py), [test_accessibility_gates.py](../tests/test_accessibility_gates.py), [test_preparation_confirmation.py](../tests/test_preparation_confirmation.py). |
| **REQ-11 — Ranking** | PRD individual/group/base formula, `.35` member floor, `.10` cuisine-diversity margin, versioned curated attributes and neutral missingness. Confirmed structured choices replace the original prose dimensions so deleted AI chips cannot leak back into scoring. Private features stay out of shared responses. | **Baseline implemented and formula tested.** Travel and venue quality stay neutral without evidence; incomplete personal history does not establish “never visited.” No real-world quality/fairness evaluation has been completed. | [Ranking](../dining/ranking.py), scoring contract including A23; structured-choice override, recommendation privacy and perturbation tests. |
| **REQ-12 — Feedback/memory** | Owner-scoped actual-visit observations, decay-based venue affinity, reviewable repeat proposals after three supporting meals, rejection suppression, opt-out and deletion of dependent proposals. No medical inference. | **Partial.** No complete impression/exposure event model or attribute-affinity learning pipeline. The snapshot limits retrieved observations to its bounded recent window. Full consent/version provenance and longitudinal evaluation remain incomplete. | Workflow rich-feedback/proposal/deletion/rejection tests; ranking decay/deletion/consent tests; [API](../dining/api.py). |
| **REQ-13 — Architecture** | Mobile web, authenticated Python API, isolated snapshots, real LangGraph/ranker, optional narrow model explanation and persisted recommendation/inbox/email workers. | **Partial local architecture.** SQLite/one host remains the supported pilot. No PostgreSQL migration, HA, production load or disaster-recovery proof; native provider threads cannot be forcibly killed. | [App](../webapp.py), [worker](../dining/generation.py), actual-app and job tests. |
| **REQ-14 — Agent/middleware** | Auth outside model, minimal allowlists, strict JSON validation, bounded single calls, deterministic fallback, local private/requirement routing, per-diner request limits, stale-result rejection, structural receipts, and adversarial injection resistance. | **Partial.** Synthetic live explanation, bilingual taste-draft contracts, and comprehensive adversarial input resistance verified. No provider quality set or approved external trace exporter exists. | Agent/observability and inference-budget tests; [test_adversarial_injection.py](../tests/test_adversarial_injection.py); preference endpoint concurrency/privacy tests; live-probe record in [INFERENCE.md](INFERENCE.md). |
| **REQ-15 — Data/contracts** | Scoped accounts, room/meal revisions, private responses, votes/delegation, checked/manual decisions, feedback, learning, immutable recommendation archives, and durable versioned generation/notification/email jobs. Sanitized terminal attempt receipts persist across result invalidation. | **Partial.** Local immutable recommendation payload archives (`recommendation_archives`) persist independently from meal invalidation and are queryable via `GET /meals/{meal_id}/recommendation-history`. Versioned legal consent, and deletion-tombstone/restore systems are missing. Not every mutation has a general idempotency/event contract. | [Store](../dining/store.py), [API](../dining/api.py), queue deduplication, transactional publication, archive survival ([test_api.py](../tests/test_api.py)), and deletion tests. |
| **REQ-16 — Notifications** | In-app inbox/polling, opt-in check-in reminders, post-meal prompt, room mute, stable reminder IDs and expired/stale job suppression. | **Partial.** Web push is not implemented. No push subscription lifecycle, provider coalescing, quiet-hours delivery or full external-notification retry matrix. Account-recovery email is not meal-alert delivery. | [Notifications](../dining/notifications.py), notification tests, browser inbox access. |
| **REQ-17 — Accessibility/local usability** | Responsive UI, labels/focus/status, reduced-motion styling, RM/MYT, comprehensive Bahasa Melayu coverage (`web/i18n.js`), contrast corrections, keyboard dialogs and in-memory private draft recovery. | **Partial.** Comprehensive Bahasa Melayu interface translations and bilingual browser testing covered. Screen-reader, physical device, 200% zoom and full WCAG acceptance remain open. | Standalone [browser suite](../tests/browser/dining.spec.cjs), [translations](../web/i18n.js), styles and prior CUA checks. |
| **REQ-18 — Security/privacy boundaries** | Server-owned identity, room checks, CSRF, opaque sessions, revocation, source gating, minimal model input, restrictive browser headers, private file permissions, account export/deletion, and full-route authorization matrix. | **Partial.** Local SQLite is not encrypted at rest. Production TLS/storage/secret management, retention deletion jobs, backups/restore and a complete attack/trace leakage review are not done. Full-route authorization matrix verifies access boundaries. | API security/isolation/deletion tests, [test_authorization_matrix.py](../tests/test_authorization_matrix.py), account-email tests, agent/observability sentinel tests, [app boundaries](../webapp.py). |
| **REQ-19 — Malaysia launch review** | The draft PRD documents proposed privacy/safety obligations; the local UI explains basic privacy choices. | **Not accepted.** Controller/applicability assessment, bilingual notices, processor/transfer inventory, incident/support owner, DPO/registration assessment and privacy-impact review require actual operator/legal work. | Original PRD §14.3; no automated test substitutes for these deliverables. |
| **REQ-20 — Observability/evaluation** | Restricted operator summary, worker health, durable job/attempt receipts, current stage durations, model-call counts, recommendation archive count, and provider/unknown token usage. No raw private snapshots. | **Partial.** No cohort outcomes, exposure denominators, real fairness/quality evaluation, cost-rate accounting or approved LangSmith pipeline. Bounded operational views are exposed; comprehensive evaluation dashboards remain open. | [Observability](../dining/observability.py), observability invalidation/receipt privacy test; job metadata tests; scoring A23. |

## Acceptance cases A01–A26

“Covered” below is limited to the described offline case. Missing parts remain
release work even when nearby tests pass.

| Case | Evidence and actual scope | Acceptance conclusion |
|---|---|---|
| **A01 — Cross-user/room access** | API `test_cross_room_isolation_and_private_projections`, [authorization matrix suite](../tests/test_authorization_matrix.py), export/deletion checks and workflow learning-owner checks. | **Covered across every authenticated API route.** Comprehensive access matrix independently verifies unauthenticated, expired, outsider, uninvited, and removed member states, asserting private projection isolation. |
| **A02 — Duplicate creation/retries** | Creation idempotency plus job same-snapshot replay/retry tests; one transactional ready notification. | **Covered for local API/worker behavior.** External push acknowledgement is outside this implementation. |
| **A03 — Silent invitee** | API `test_pending_invitation_needs_explicit_exclusion`; workflow persists original invitation count. | **Automated coverage for readiness/exclusion.** Outcome metric denominators are not fully implemented. |
| **A04 — Anything** | Ranking `test_anything_is_explicit_neutral_not_absent_data`; hard checks run independently. Browser friend submitted “anything.” | **Covered for neutral taste behavior and the checked browser path.** Complete combinations of saved requirements are not exhaustively tested. |
| **A05 — Stale generation** | Profile/membership invalidation, queued context supersession, in-flight evidence replacement and rejected old lease output. | **Automated local coverage.** Production distributed-failure campaign remains separate. |
| **A06 — Missing dietary evidence** | Recommendation `test_no_allergy_guarantee_from_scraped_menu`, `test_unknown_not_pass`, private verification explanation and blocked-import tests. | **Covered conservative behavior.** No claim that an allergy-confirmation workflow has been implemented. |
| **A07 — Hard cap/ingredient requirements** | Recommendation `test_missing_charges_and_budget_cannot_pass`, `test_free_text_avoid_is_not_guessed_safe`, reviewed-dietary-conflict and minimum-order tests. | **Automated gate coverage.** The app blocks unresolved requirements; it does not resolve preparation safety or use actual popularity data. |
| **A08 — Same suitable order satisfies price and diet** | Dedicated fixture: a cheap nonvegetarian dish plus an over-budget vegetarian dish gives no eligible option; lowering that same suitable dish’s full price permits it. | **Covered offline.** This does not verify actual restaurant dietary statements. |
| **A09 — Stale vote/selection** | Context/catalog invalidation plus evidence-only replacement advances revision, clears old votes/delegation and rejects delayed writes. Idempotent repeat preserves current consent. | **Covered for local tested changes.** Current final revalidation still runs before selection. |
| **A10 — Private veto** | Workflow `test_three_state_votes_and_scoped_delegation_respect_hard_veto_privately`; executed browser private-veto journey. | **Covered offline and in the stated browser journey.** No majority or organizer override. |
| **A11 — No attendance, no taste learning** | API feedback visit check; workflow `test_rich_feedback_never_infers_taste_from_nonattendance_or_service`. | **Covered offline.** Nonattendance is not a negative enjoyment observation. |
| **A12 — Attribute-specific meaning** | Workflow nonattendance/service test and rich feedback fields; learning writes venue outcomes without inferring cuisine dislikes. | **Partial.** Service-only feedback does not teach cuisine dislike. Full explicit attribute-affinity learning is not implemented. |
| **A13 — Delete/opt out of learning** | Workflow supporting-observation deletion, clear-learning and ownership tests; API memory opt-out; ranking `test_removed_observation_recomputes_affinity_without_cached_influence`. | **Covered for current local stores/derived venue signals.** No external vector index or backup-restore propagation was tested. |
| **A14 — Prompt-injection boundary** | Agent `test_model_only_receives_option_ids_and_allowed_labels`, [adversarial injection suite](../tests/test_adversarial_injection.py); no model-bound arbitrary tool/database interface. | **Covered across hostile sources and inputs.** Malicious fixtures in restaurant names, menu descriptions, reviews, and evidence cannot change deterministic eligibility or extract private sentinels. |
| **A15 — Invented venue/claim** | Agent `test_invented_model_output_is_not_published_or_retried`. | **Covered with a fake model response.** Live-model/provider integration remains unverified. |
| **A16 — No push/device fallback** | App uses inbox/link without requiring push; executed local browser workflow does not use push. | **Partial.** The full unsupported-browser/iPhone Home Screen/permission-denial matrix has not been run. |
| **A17 — Notification retry/expiry** | Notification opt-in/reconstruction/deduplication, mute/expiry and feedback-timing tests. | **Partial.** No push provider exists, so ambiguous provider acknowledgement, device coalescing and subscription invalidation are not covered. |
| **A18 — Disconnect/restart** | Queue reconstruction, lease recovery/fencing, bounded retries, no duplicate publication; browser reload during generation restores durable state. | **Local coverage added.** No production chaos campaign; a permanently blocked native provider requires restart and reports degraded worker health. |
| **A19 — Sensitive-data leakage** | Observability tests inject private health/GPS/error markers; model input is constrained; blocked source content is absent from shared responses. | **Partial.** No exported LangSmith/child-trace pipeline or push/client-analytics pipeline has been enabled and tested. Disabled channels must not be described as a completed export review. |
| **A20 — Accessibility/reflow** | Axe A/AA scans on auth/profile/check-in/shortlist/dialog views, native keyboard controls/focus return, horizontal reflow and three browser configurations. | **Partial.** Automated scans and emulation do not prove complete screen-reader, 200% zoom, physical-device or full WCAG acceptance. |
| **A21 — Deletion/restore** | API account deletion cascades and scrubs shared future state; email tables cascade on deletion. | **Partial.** Backups, restore and deletion-tombstone replay are not implemented/rehearsed. |
| **A22 — Hours/holidays/kitchen** | Dated-service tests cover closures/overrides, unknown coverage, overnight spillover, arrival cutoffs, finish, source expiry/rights and malformed evidence references. | **Covered offline for modeled intervals.** Adjacent shifts are conservatively not joined; real branch facts remain unverified. |
| **A23 — Exact scoring examples** | Ranking `test_a23_exact_prd_worked_examples` checks group `.7200/.6500` and base `.687/.6275` with neutral Q/N. | **Covered by the explicit mathematical contract test.** These fixture utilities are illustrative, not observed diner satisfaction. |
| **A24 — Original denominator/deadlines** | Workflow `test_profile_conflict_and_logistics_edit_preserve_original_deadlines` and overdue/expiry test. | **Partial.** Original deadlines/invite count persist, but complete eligibility-denominator analytics and every exclusion/cutoff event are not implemented. |
| **A25 — Shared-device account change** | In-memory drafts clear on logout/expiry/account change; request epochs reject late results, and cross-tab session changes trigger identity refresh. Browser case delays a real old inbox response across account switch. | **Browser coverage added for named paths.** No push subscription detach until push exists; no claim about every shared-device/browser-cache scenario. |
| **A26 — Requirement deletion** | Profile consistency validation and profile edits invalidate responses/snapshots; reconfirmation tests cover active and upcoming selected meals. Dedicated acceptance case `test_a26_deletion_of_allergy_asserts_post_deletion_review_state_and_owner_confirmation` validates allergy gating, post-deletion participant re-confirmation, subsequent shortlisted generation, and reconfirmation requirement on modified selection. | **Covered by dedicated acceptance test.** Post-deletion requirement re-confirmation and upcoming meal reconfirmation lifecycle verified. |

## Browser evidence recorded for this change

The implementation task executed a local browser journey with fictional accounts
and catalog data:

1. Open a room containing **nine members** and invite a **two-person subset**.
2. Submit independent private check-ins through each person's own interface.
3. Generate using the application's actual graph and deterministic ranker.
4. Have one participant privately choose **Cannot eat here**; verify that the
   organizer cannot select and cannot see the participant's private reason.
5. Change that response to **Works for me** and confirm the unanimous decision.

This journey passed as reported by the implementation task. It does not cover the
complete post-meal flow, every new email screen, all phone/browser combinations,
live restaurant data or all PRD acceptance cases.

[scripts/browser_e2e.js](../scripts/browser_e2e.js) contains the repeatable CUA
journey. It expects two already-provisioned CUA browser tabs with isolated cookies
(`127.0.0.1` and `localhost`) and an explicit fixture configuration. This older CUA helper is **not** the standalone runner; `node scripts/browser_e2e.js`
does not execute its assertions. The new `npm run test:e2e` suite does. [seed_browser_e2e.py](../scripts/seed_browser_e2e.py) creates
disposable accounts through the actual local API and refuses a non-demo server.
Use a separate test database; these fixtures do not belong in a real pilot.

## What prevents an external pilot today

1. **Usable, approved local data:** resolve source permissions and disputed records,
   verify useful branch/menu/location/hours coverage for recruited groups, and
   staff data-error/dietary-evidence review. Schema-valid JSON alone is insufficient.
2. **Production operations:** decide and test storage/deployment architecture;
   establish HTTPS, protected secrets/encryption, retention, backups/restore,
   deletion propagation, quotas, monitoring and rollback. The current deployment
   is a local pilot, not production acceptance.
3. **Identity and delivery:** configure optional SMTP and validate delivery and
   recovery with an operator-controlled mailbox before requiring verified email.
   Implement push only with its permission, expiry, retry and account-switch rules;
   inbox remains the usable fallback.
4. **Remaining product work:** complete supported requirement-verification paths,
   route/accessibility evidence where promised, full bilingual wording, missing
   lifecycle/data contracts and the PRD outcome/fairness metrics. Keep absent
   capabilities explicit rather than substituting invented evidence.
5. **Release evidence:** add missing acceptance fixtures and the standalone browser
   suite in remote CI; run the declared iOS/Android/browser/accessibility matrix. Complete the
   Malaysia privacy/provider/incident review and an end-to-end sensitive-data
   audit before inviting external users.

The original PRD §17.2 remains the external-pilot release gate. A working local
happy path and a passing automated suite do not, by themselves, satisfy it.

## Latest durability and browser build

The durability update added durable generation, dated service/kitchen checks,
private route-scoped drafts, account-response isolation, and a standalone browser
runner. The job suite specifically covers queued restart, expired leases, late output,
bounded failures/timeouts, evidence-only consent invalidation, deleted participants
and hostile receipt values. The operator view retains sanitized receipts after
current-result invalidation.

Browser execution results are recorded in `E2E.md`; the suite runs scenarios
across desktop Chromium, 320px narrow Chromium and mobile WebKit emulation.
GitHub Actions is configured but was not run remotely in this task. Real SMTP/push/catalog
accuracy, physical devices, screen readers, complete Malay review and production
release gates remain open.

## ILMU configuration and honest inference status

The inference build supports the requested `ilmu-mini-v3.3` through explicit
server-side configuration. The model can select approved explanation labels after
deterministic ranking; it cannot interpret cravings or rank venues. Home configuration
status is separate from each meal's actual recorded model use. Historical jobs and
the restricted operator view retain sanitized provider/model, status and usage fields.
A changed inference configuration changes generation identity, while a usable
credential rotation alone does not.

Backend additions include a full queued app flow with a simulated provider,
configuration/probe tests, output validation/privacy checks, and real ChatOpenAI
HTTP tests against a loopback fixture. The browser journeys force inference off
and assert that the UI correctly reports no LLM use. Two synthetic ILMU calls also
validated the explanation-label and Bahasa Melayu taste-draft contracts with
provider token usage. These are narrow transport/output checks, not live restaurant
recommendation evidence, a provider quality evaluation or complete PRD acceptance.
Remaining PRD gates above are unchanged.

## PRD gap implementation pass

The PRD gap implementation pass added:
- **Minimal pre-join room preview (`GET /rooms/preview`):** returns table name, host name, member count, and creation time without exposing member identities or past meal history; UI preview step before joining.
- **Shortlist selection modes (`choice_mode`):** PRD §5.6 deterministic score tie-breaking (`choice_mode='tie_break'`) preferring higher fit score, lower distance, greater evidence coverage, and stable venue ID; agreed random draw (`choice_mode='random_draw'`) across unanimously accepted options; and recorded `choice_mode` in the shared decision view.
- **Dietary/data-error investigation reporting (`POST /meals/{id}/report-data-error`):** logged into `data_error_reports` and restricted operator view with category and description under status `investigating`, emitting an audit event without polluting user taste learning; confirmed alternate branch linkage (`outcome='somewhere_else'`, `alternate_outlet_name`) in after-meal feedback.
- **Immutable recommendation archives (`recommendation_archives`):** persists full recommendation and evidence payloads during both worker-based and synchronous generation, queryable via `GET /meals/{id}/recommendation-history` even after subsequent context changes clear `meals.result`.
- **A26 allergy deletion acceptance test:** `test_a26_deletion_of_allergy_asserts_post_deletion_review_state_and_owner_confirmation` validates allergy gating, post-deletion participant re-confirmation, subsequent shortlisted generation, and reconfirmation requirement on modified selection.
- **Privacy-preserving schedule conflict warnings (`schedule_conflicts`):** calculates and displays overlapping meal schedule warnings without disclosing cross-room member or session details.
- **Unified end-to-end journey test (`test_full_e2e_journey.py`):** validates the complete lifecycle across 21 contiguous stages from registration, room preview/join, check-in with `must_leave_by`, deterministic shortlist retrieval, archive survival, data-error reporting, veto and tie-break selection, finish transition, post-meal inbox prompts, feedback and alternate branch linkage, repeat venue proposals, 7-day closure, export, and deletion cascades.

Current test counts should be read directly from test runner execution (`pytest tests -q`).

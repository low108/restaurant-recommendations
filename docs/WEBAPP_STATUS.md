# Web app implementation status

Implemented 5 October 2026. This is a local pilot implementation, not a public
production deployment or a declaration that every PRD release gate is satisfied.

## Working path

Account → private preferences and explicit requirement review → create/join table
→ meal date/time and shared point → individual check-ins → freeze included people
→ deterministic checks/ranking → private approvals → unanimous confirmed decision
→ post-meal feedback. Inbox, membership changes, expiring/revocable invites,
ownership transfer, account export and deletion support this path.

The mobile UI uses local SVG illustrations and no remote fonts, analytics, images
or private browser storage. The Python service uses the existing LangGraph/Ollama
integration. SQLite is added for atomic persistent multiuser application state;
the previous prototype had JSON source files but no transactional operational DB.
The catalog remains a separate, validated JSON import. No restaurant scraping was
performed in this implementation session.

The restaurant collector handoff is in `SCRAPING_HANDOFF.md`. Version-2 schema (with version-1 loading compatibility) and
fictional examples are source artifacts; independently collected data
is delivered outside this checkout and reviewed before import.

## Recommendation behavior and limits

The baseline considers every imported outlet within the specified geographic
radius. Distances are straight-line, never ETA or road distance. Every candidate
needs current display-authorized evidence, published opening intervals covering
the meal, date-scoped exception evidence, kitchen last-order evidence and suitable
complete-priced meals. Holiday overrides and overnight finish are checked explicitly. Strict halal
requirements need outlet certification evidence. Allergies and arbitrary hard
avoid text stay in verification because there is no order-specific restaurant
preparation confirmation channel in this pilot.

Menu matching uses curated aliases and explicit attributes. The versioned PRD
baseline weights current craving, lasting taste, travel, spending and occasion;
uses a 60/40 mean/minimum group blend; and applies the specified fit floor and
cuisine-diversity rule. Missing routes and quality evidence stay neutral. These
weights remain hypotheses. Consented venue affinity uses reversible observations
and 90-day decay; this is not a trained ranker or fair-exposure guarantee.
Duplicate and irrelevant-menu perturbation tests cover specific failure modes,
not all dataset biases or real-world relevance.

Optional Ollama selects only permitted reason labels and cannot change critical
claims. The deterministic result is used when no model is available. LangGraph
state contains private data internally; tracing is disabled at that boundary.

The ILMU configuration also supports an optional taste-draft step. The diner sees
and approves the exact craving text before it is sent; apparent requirement,
contact and secret text is held locally. Returned allowlisted choices remain
unsaved until the diner applies, edits and submits the normal check-in. Confirmed
structured fields then override the original prose dimensions in ranking.

## Before inviting real users publicly

- Configure and verify the optional SMTP account verification/recovery service,
  then enable the verified-account requirement. Without this configuration, email
  remains an unverified login identifier. See `OPERATIONS.md`.
- Configure HTTPS, restricted host names, protected/encrypted persistent storage,
  backup/restore/deletion procedures, retention jobs and operational monitoring.
  SQLite files currently hold plaintext application data behind OS file access.
- Review source rights, catalog facts, dietary evidence and the actual Malaysia
  privacy/consent policy. Imported permission flags are operator assertions, not
  an automated legal review.
- Add sustained abuse controls and production worker/storage validation before multiworker
  deployment. Durable recommendation, inbox and email queues now work locally. The current server is a loopback-bound, single-process pilot.
- Complete and locally review Bahasa Melayu translations. Core labels/forms now
  have a language switch, while some explanatory notices remain English.
- Web push, route providers, restaurant verification workflows and external
  LangSmith export remain disabled. Durable opt-in check-in reminders and
  automatic post-meal prompts now work through the in-app inbox.
- Venue preference proposals require repeated explicit actual-visit feedback and
  owner review. Full attribute-specific learning, exposure analytics and fairness
  evaluation remain unfinished; no medical/dietary inference is performed.
- No payment, reservation, booking, outside messaging, deployment or Git push
  occurred. The original course Gradio/MCP/Chroma prototype remains available.

## Verification

The test suite covers catalog reference consistency, rights/freshness states,
price evidence, menu-size perturbations, overnight hours, scoped authentication,
CSRF, room isolation, stale generation, membership edits, unanimous selection,
revoked votes, catalog changes, deletion and interrupted-run recovery.
Browser QA checks signup, profile editing, room/meal flow, errors and 320px layout.
Synthetic accounts/catalogs used for tests do not establish live restaurant quality.
New real-application integration tests exercise the graph through HTTP routes,
including CAS conflicts, scoped delegation, reconfirmation, manual plans and
feedback correction. The nine-member room → two diners → real ranking → private
veto → unanimous choice browser journey passed. See `E2E.md` for repeatable
fixtures and the distinction between manual CUA, standalone automated browser
checks and actual-device acceptance.

On 6 October 2026, 270 backend tests and 24 browser cases passed. Two
synthetic live ILMU requests validated the explanation-label and bilingual
preference-draft output contracts. They do not establish restaurant quality,
production reliability or model recommendation quality.

The same build adds PRD question M09 without another model call. A diner may ask
for one optional follow-up after the core check-in. The server compares current
viable options under bounded cuisine counterfactuals and asks only when at least
two answers produce different preliminary orders. Questions and answers are
owner-scoped, revision-checked and contain no restaurant identities.

Confirmed meals now move into `awaiting_feedback` at their planned finish and
become `closed` after seven days. The API and notification worker persist the same
transition, checked and manual decisions remain distinguishable, reminders survive
the intermediate state, and closing preserves history while stopping new feedback.

## Workflow completion pass

The UI now selects meal invitees independently from room membership, supports
Decide later and explicit usual preferences, and preserves drafts during response
conflict recovery. Votes distinguish Works for me, Prefer another and Cannot eat
here; private reasons stay private. Group-choice delegation is scoped and revocable.
Original deadlines survive edits, overdue/expired states are explicit, and material
changes to an upcoming selected meal require reconfirmation. Groups can record a
manual unverified plan only after each included diner acknowledges that status;
such plans never become checked recommendations.

Private feedback now records enjoyment, repeat intent, fairness, cost and reasons.
Learning has an owner-facing review/delete/clear flow with source-linked proposals.
Agent stages have structural receipts, accurate unknown token-usage states and a
restricted local operations view. `PRD_ACCEPTANCE.md` is the detailed completion
matrix; no claim of full PRD or external pilot readiness is made.

## Interface refresh

The app uses the supplied UNIQLO app screenshot as a visual reference: white
backgrounds, black pill buttons, thin gray rules, simple line icons and generous
spacing. The red Makan mark and friends-sharing-food illustration are original
local assets. The app retains its own identity and has no affiliation with UNIQLO.
Mobile navigation stays at the bottom; desktop navigation runs across the top.
Confirmed meals show the decision before readiness details on small screens.
Form labels, keyboard focus, reduced-motion support and safe-area spacing remain
available. This is a presentation change; accounts, data collection, agent tools
and recommendation policies are unchanged.

## Real-catalog integration

Schema v2 records category, serving range, price unit/minimum quantity and review
status. The CLI can upgrade v1 without approving unknown claims and generate an
operator audit report. A limited text conflict screen quarantines contradictory
vegetarian/vegan records; absence of a flag is not dietary validation.

The current user-provided import is held for review. Public routes expose only
aggregate coverage until sources support publication. The UI displays those counts
and concrete gaps without exposing source content or private requirements.
Only independently purchasable one-person main meals/sets are budgeted. Shared
dishes, piece/weight pricing and minimum quantities above one require additional
order allocation support, rather than being misrepresented as per-person prices.
The live agent still uses structured matching; no real embeddings were generated.
See CATALOG_V2.md for the captured input version and repeatable import commands.

## Durability and browser acceptance pass

Generation now runs from a persisted, idempotent queue, with bounded retries,
lease/restart recovery and transactional publication. Sanitized attempt receipts
remain after the active shortlist changes. Every new evidence-based set advances
its consent revision. Stalled native provider calls expose a delayed-worker state;
see `OPERATIONS.md` for the explicit thread/isolation limit.

The frontend keeps unsent private drafts in memory across route navigation, guards
late responses by account and route, and clears private state on sign-out/expiry or
another tab changing accounts. Toast, step/readiness and mobile navigation contrast
were corrected using automated axe findings. Full translations, screen readers and
physical device testing remain release work.

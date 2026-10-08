# Local operations and account email

The app remains a single-process local pilot. The server checks durable inbox
reminder jobs every 30 seconds. Opted-in incomplete diners receive one reminder
before the current check-in cutoff; expired, withdrawn and muted reminders are
suppressed. One feedback inbox prompt becomes due at planned finish plus one
hour. Delivery and its stable inbox ID commit together. No browser push provider
is enabled.

## Restricted monitoring

Set `DINING_OPERATIONS_TOKEN` to independent random secret material of at least
32 characters, restart, and open `/operations`. Paste the token into the local
form when loading status. It is cleared after each request and never saved to
browser storage. Keep the token out of source control and shared screenshots.
Without it, the API refuses operations access. A regular diner session does not
grant operator access.

The view shows meal-state totals, inbox-job states, worker health, up to 50 recent
durable generation jobs with attempt receipts, and 100 current stored results.
Completed receipts remain when the active result is invalidated. It reports coarse
stage timings and model usage. Unknown
provider usage is null, never a fabricated zero. Raw prompts, health details,
private answers, coordinates and restaurant prose are not exported. Structural
receipts describe processing, not factual restaurant verification. These counts
are not the PRD's cohort-based success/fairness metrics or a complete historical shortlist archive.
LangSmith export remains disabled.

Inference configuration and each recorded attempt are shown separately. `ready`
means settings are complete; `validated` on an actual result means its explanation
labels passed validation. The model currently selects labels only; eligibility
and ranking remain deterministic. See [ILMU setup and receipt meanings](INFERENCE.md)
for the private environment file, synthetic probe and pending live verification.

## Optional verification and password recovery

Email stays unavailable until an operator supplies all required configuration:

| Variable | Purpose |
| --- | --- |
| `DINING_SMTP_HOST` | Approved SMTP server |
| `DINING_SMTP_FROM` | Approved sender email |
| `DINING_PUBLIC_BASE_URL` | App HTTPS origin; HTTP allowed only on loopback |
| `DINING_EMAIL_TOKEN_SECRET` | Independent random secret of at least 32 bytes |
| `DINING_SMTP_PORT` | Default 587 |
| `DINING_SMTP_TLS` | `starttls` (default) or `ssl`; plaintext is refused |
| `DINING_SMTP_USERNAME`, `DINING_SMTP_PASSWORD` | Optional paired SMTP credentials |
| `DINING_REQUIRE_VERIFIED_EMAIL` | `true` restricts shared dining to verified accounts; default `false` for local testing |

Do not put real credentials in this document. Configure the approved service via
the deployment's secret management. This change did not create an email account,
configure a provider, or send a real email.

Users explicitly request their verification/reset email. Requests enqueue a
bounded durable job; the worker performs at most three delivery attempts. Generic
responses avoid revealing whether an address is registered. Tokens are single-use,
expire after 24 hours (verification) or 30 minutes (reset), and are transported in
URL fragments to avoid HTTP access-log leakage. Password reset revokes all existing
sessions and requires a new sign-in. Unverified users retain access to their own
profile, verification, export and account deletion even when shared dining is gated.

SMTP integration is tested with a fake mail service. Verify actual delivery,
domain authentication, hosting/TLS, bounce handling, provider terms and incident
procedures before inviting real users. Backups, at-rest encryption, retention
jobs and the external pilot's privacy assessment are still release work.

## Durable recommendation worker

The app checks the recommendation queue every second, independently from the
30-second reminder/email sweep. `POST /api/meals/{id}/generate` returns **202**
and a meal view with `generation_job`, `generation_history` (latest ten jobs), and
`generation_worker_status`. Reloading `GET /api/meals/{id}` restores progress.

A job is unique for meal/context revision, evidence identity (including inference
configuration) and ranking policy.
Repeating the request returns the same job and does not send duplicate notices.
Evidence-only replacement advances the meal revision too, so delayed votes and
old delegation cannot approve the new shortlist. Authorization, membership,
profile/response versions and critical evidence are checked before execution and
again before publication. Changes supersede the attempt; late output is discarded.
Publishing the current result and its inbox notices is one transaction.

The default job permits three attempts total, with five- then ten-second retry
backoff. A logical attempt has a 60-second deadline and a 75-second lease. A dead
worker's lease must expire before another worker can claim its work. Compare-and-
swap on the lease token prevents a late old worker from publishing. Restarting a
server does not reset the attempt budget. Existing pre-worker interrupted runs
are migrated back to a retryable planning state.

Attempts retain status, timestamps, duration, bounded error codes and allowlisted
counts/structural stage receipts. They do not retain private profiles, answers,
coordinates, model messages or raw exception text. Live private inputs are rebuilt
from version-checked records. Transient input references/fingerprints are cleared
when a job completes or is superseded, including deletion invalidation. Job history
is a processing record, not a historical copy of every restaurant recommendation.

**Thread limitation:** Python cannot forcibly stop a provider that ignores its
own timeout. If it continues after the logical deadline, this worker stops starting
new calls, reports `waiting_for_provider_shutdown`, and the UI shows processing
is delayed. Normal optional model requests have a 15-second transport timeout.
If an adapter never returns, an operator must restart the process; queued work then
recovers after lease expiry. This resource cap prevents accumulating hung calls; it
is not hard process isolation or proof of production failover.

SQLite/one-host operation remains the supported pilot deployment. Unit tests
exercise two workers and lease fencing, but production multi-process load,
PostgreSQL migration, retention/backups/restore and external providers still need
separate validation.

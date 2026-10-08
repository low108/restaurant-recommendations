# ILMU inference: setup and what is actually tested

The requested model is **`ilmu-mini-v3.3`**, matching the earlier DearFlow setup.
It is supported by the web app's configurable OpenAI-compatible adapter. On
6 October 2026, two synthetic live requests to the configured ILMU endpoint were
validated: the explanation probe used 86 input / 33 output tokens, and a Bahasa
Melayu taste draft used 704 input / 94 output tokens. This proves those two calls
worked at that time; it does not establish recommendation quality or availability.

## What the model does today

1. Python checks the catalog's evidence, each person's requirements, budget and
   distance, then ranks the eligible restaurants using the documented baseline.
2. If there are eligible options and inference is configured, ILMU receives only
   opaque option IDs and three approved reason IDs: `group_fit`, `budget`, `nearby`.
3. ILMU chooses two distinct reason IDs for each option. The app converts those
   IDs into fixed explanation text. It rejects additional options, unknown reason
   IDs, repeated keys, malformed JSON, oversized output, refusal or truncation.
4. If inference is disabled, incomplete or fails, the existing template reasons
   remain. Restaurant eligibility, order, scores, prices and evidence stay under
   Python's control.

The check-in also offers a separate, optional **taste draft**. After an explicit
preview and consent step, the model receives only that diner-entered craving text
and fixed allowlists. It can propose editable cuisine, dish, flavour, appetite,
spice, comfort-budget, occasion and novelty fields. The proposal is not saved or
ranked until the diner applies it, reviews every field and submits the ordinary
check-in. Apparent food requirements, contact details and secrets are blocked
locally with zero model calls. Profile requirements, room data, location, restaurant
records and menu prose are never included. The model cannot change firm budgets,
requirements, eligibility, ranking, evidence or restaurant claims.

## Configure the private server file

From the repository root, after installing `requirements-web.txt`:

```bash
mkdir -p var
cp config/inference.env.example var/inference.env
chmod 600 var/inference.env
```

Edit `var/inference.env` locally and fill `DINING_LLM_API_KEY`. Do not paste a key
into chat, commit it, or put it in frontend JavaScript. If this local file already
exists, edit it instead of copying over it. `var/` is ignored by Git.

| Setting | Meaning |
| --- | --- |
| `DINING_LLM_PROVIDER=ilmu` | Explicitly selects ILMU; the default with no configuration is `disabled`. |
| `DINING_LLM_MODEL=ilmu-mini-v3.3` | Exact user-selected model ID; no automatic replacement if it fails. |
| `DINING_LLM_BASE_URL=https://api.ilmu.ai/v1` | ILMU's documented public base URL. Use the account's actual gateway if different. |
| `DINING_LLM_API_KEY` | Server-only credential; required for remote providers. |
| `DINING_LLM_TIMEOUT_SECONDS=15` | Transport timeout, allowed range 1–60 seconds. |
| `DINING_LLM_MAX_OUTPUT_TOKENS=384` | Output cap, allowed range 64–1024. |

The official [ILMU quickstart](https://docs.ilmu.ai/docs/getting-started/quickstart)
documents its OpenAI-compatible endpoint and bearer authentication. This integration
uses [LangChain's ChatOpenAI adapter](https://docs.langchain.com/oss/python/integrations/chat/openai).
Compatibility of this specific model must be established by an actual probe.

The app loads **only** the explicitly supplied `--env-file`; it does not search
other projects for credentials. Exported environment variables take precedence.
Values use python-dotenv syntax; `${...}` interpolation is disabled so secrets remain
literal. Changes need a server restart. Legacy `OPENAI_API_KEY`/`OPENAI_BASE_URL`
are fallbacks only when `ilmu` or `openai_compatible` is explicitly selected.

## Check configuration, then make one real request

```bash
# No network request. Reports whether configuration is complete.
.venv/bin/python -m dining.llm.check --env-file var/inference.env

# Explicitly sends one small fictional request; provider usage may be charged.
.venv/bin/python -m dining.llm.check --env-file var/inference.env --send

# Start the app using the same configuration.
.venv/bin/python webapp.py --demo --env-file var/inference.env --port 7861
```

A successful probe reports `status: validated`, `provider_contract_verified: true`,
`model_calls: 1`, and provider token counts when supplied. It verifies that this
configured provider can answer the synthetic explanation contract. It does **not**
prove recommendation quality, dietary facts, or the complete dining journey.
Missing configuration or failed inference returns exit code 2 with a bounded error
code. Raw errors, prompts, provider output, credentials and endpoint are not printed.

Then use two fictional diners to submit check-ins and generate a fresh shortlist.
The meal should show **“Explanation labels selected by ILMU”** only if that actual
generation succeeded. Existing results keep their historical status. A changed
provider/model/endpoint or configuration readiness changes the job identity when
generation is requested again; rotating a usable key alone does not.

Use `--catalog var/catalog.real.v2.json` instead of `--demo` for the collected catalog.
The current collected data still needs evidence review; if no options qualify,
the model is skipped. Configuring ILMU does not remove catalog evidence requirements.

## Reading the status and receipts

`GET /api/inference/status` and the home screen show configuration, not proof of a
successful request. Each meal result and the restricted `/operations` page show
what happened in the recorded run.

| Field/value | Simple meaning |
| --- | --- |
| `configuration_status: ready` | Required settings are present and structurally valid; connectivity is untested. |
| `disabled` | The model was switched off. |
| `not_configured` | A required setting is missing or invalid; zero calls. |
| `skipped_no_options` | No eligible options needed explanations; zero calls. |
| `inference_status: validated` | The returned explanation IDs passed validation. |
| `provider_error` | Provider/transport failed; template explanations used. |
| `invalid_output` | Provider replied, but the response failed the output contract; templates used. |
| `model_status: template_fallback` | Compatibility summary for the two failure states above. |
| `provider`, `model_id` | Configured provider and requested model. |
| `returned_model_id` | Model ID reported by the provider when available; not independent proof of model identity. |
| `role` | `explanation_labels_only` for shortlist wording or `preference_draft` for an editable, owner-confirmed taste proposal. |
| `model_calls` | Number of attempted adapter invocations; it is not proof the provider completed billing. |
| `input_tokens`, `output_tokens` | Provider counts when supplied; zero if no call, `null` if called but unknown. |
| `usage_source` | `provider`, `not_called`, or `unknown`. No invented token estimates. |
| `model_error_code` | Bounded configuration/failure code; never the raw provider error. |
| `prompt_version` | Contract version for the allowed-label prompt. |
| `estimated_cost: null` | No verified account-specific price is configured, so cost is not guessed. |
| `trace_export: disabled` | Native LangSmith export is disabled, including the standalone probe. |

One provider invocation is allowed per inference attempt, with SDK retries disabled.
A durable job can retry other processing failures; this is not an exactly-once
billing guarantee after a process crash. Historical job receipts retain allowlisted
structural metadata, not full messages or private inputs. Legacy records lacking
inference fields show unknown rather than borrowing today's configuration.

## Verification boundaries

- The current 270 backend tests and 24 browser cases test the deterministic
  recommender, app flow, consent/review behavior and simulated providers. Browser
  tests deliberately stub or disable remote inference.
- M09 adaptive follow-ups do not call ILMU. They compare bounded counterfactual
  rankings locally and issue one owner-scoped question only when its choices can
  change the preliminary order.
- Adapter tests cover missing configuration, strict output validation, fallback,
  secret redaction, usage handling and tracing suppression using fake responses.
- HTTP protocol tests use the real ChatOpenAI transport against a local fixture.
  They verify serialization, endpoint/auth headers, token limits and errors, but
  cannot prove remote ILMU availability or behavior.
- Application tests exercise queue → actual graph → simulated provider → persisted
  result → operator receipts. Browser tests force inference off and assert the
  displayed zero-call status.
- Two live synthetic ILMU contracts passed on 6 October 2026. They did not include
  real personal data or restaurant facts and are not a quality, safety, uptime,
  billing or full-journey evaluation. Test scope is recorded in
  [PRD acceptance](PRD_ACCEPTANCE.md) and [E2E](E2E.md).

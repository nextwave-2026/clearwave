# C5 - Notification and escalation payload

C5 is produced by W4 from one C3 incident record and, when available, the latest C4
investigation result. It has no consumers inside the system: it is the terminal output that
reaches Slack and the phone channel, and the record W4 keeps of what was sent and what happened.

C5 never invents a figure. Every field is read from C3 or C4 and passed through unchanged;
`docs/ownership.md`'s hard rule for W4 - "it holds no domain logic" - applies here exactly as it
does to the dashboard.

## Severity-to-channel binding

Severity is read from C3 and never recomputed, re-ranked, or blended with diagnostic confidence.

| Severity | Channels |
|---|---|
| `low` | dashboard |
| `medium` | dashboard |
| `high` | dashboard, slack, phone |
| `critical` | dashboard, slack, phone |

Concrete severity thresholds that map raw metrics to these labels are owned by C3 (W2); this
binding only reads the label C3 already assigned. The binding itself is implemented in
`surfaces/escalation.py:CHANNELS_BY_SEVERITY` and exercised by `surfaces/escalation.py:escalate`.

## Escalation payload shape

The payload every channel receives (`surfaces/escalation.py:_payload`):

```json
{
  "incident_id": "inc-2026-08-29-001",
  "severity": "critical",
  "lifecycle_state": "investigating",
  "merchant_id": "merchant-a",
  "affected_cohort": {
    "merchant_id": "merchant-a",
    "provider": "provider-p2",
    "payment_method": "card",
    "card_network": "mastercard",
    "country": "CO",
    "issuing_bank": "bank-x"
  },
  "change": {
    "metric": "payment_approval_conversion",
    "expected": 0.92,
    "actual": 0.64,
    "absolute_delta": -0.28,
    "unit": "ratio"
  },
  "financial_impact": {
    "gmv_at_risk": { "amount": 28000.0, "currency": "USD" },
    "loss_per_hour": { "amount": 112000.0, "currency": "USD" }
  },
  "onset": "2026-08-29T10:00:00Z",
  "diagnostic_confidence": "medium",
  "leading_hypothesis": {
    "statement": "Provider P2 degradation is the leading explanation for the affected slice."
  },
  "competing_explanations": [
    { "explanation": "Bank X over-decline cannot be ruled out." }
  ],
  "recommended_next_action": {
    "action": "Investigate Provider P2 and collect a discriminatory provider/issuer comparison before broad rerouting.",
    "urgency": "now"
  },
  "citations": {
    "decline_breakdown": "decline_breakdown:q_decline_breakdown_ad769ee712ede28a",
    "operational_metrics": "operational_metrics:q_operational_metrics_fac6f51dc84e1668"
  }
}
```

- `incident_id`, `severity`, `lifecycle_state`, `affected_cohort`, `change`, `financial_impact`,
  `onset` come from C3 and are copied field-for-field, never derived.
- `diagnostic_confidence`, `leading_hypothesis`, `competing_explanations` and
  `recommended_next_action` come from C4's `result` when an investigation exists. They are absent
  (`null`) when no C4 result is available yet, or when the investigation `outcome` is
  `agent_unavailable` - the payload still carries incident facts and financial impact so the
  dashboard, Slack and phone channels can render something honest without a narrative.
- `severity` and `diagnostic_confidence` are never combined into one score or badge. This mirrors
  the C3/C4 split and PRD section 11.
- `citations` maps each evidence tool actually queried to `tool:query_id`, reduced from the
  investigation's persisted evidence trail (`investigation/store.py:read_result`'s `trail`, one
  entry per tool, first occurrence wins). It reflects every tool queried during the investigation,
  not only the ones the narrative happened to cite - `surfaces/escalation.py:_citations_from_trail`.
  Empty when no investigation result (with a trail) is available yet.

## Escalation event record

One record per channel per incident, persisted by `surfaces/store.py` in the shared SQLite file
(`escalation_event` table) so repeat reads return the same recorded outcome instead of re-firing:

```json
{
  "channel": "slack",
  "status": "delivered",
  "payload": { "...": "the escalation payload above" },
  "detail": null,
  "created_at": "2026-08-29T21:10:00.000Z"
}
```

`status` is one of:

- `delivered` - the channel accepted the message.
- `not_configured` - Slack has no webhook URL in the environment; the payload is logged instead of
  sent.
- `fallback_dashboard` - the phone channel has no Twilio credentials configured; the call is
  recorded as a pending dashboard item instead (`surfaces/store.py:list_pending_calls`) rather than
  silently dropped.
- `failed` - the channel was configured but the send raised; `detail` carries the exception text.

## Channel implementations

- **Slack** (`surfaces/escalation.py:notify_slack`, `slack_blocks`): posts a Block Kit message to
  an Incoming Webhook URL read from `CLEARWAVE_SLACK_WEBHOOK_URL` (channel name from
  `CLEARWAVE_SLACK_CHANNEL`, defaulting to `#control-tower`). The message is one `attachments` entry
  colour-coded by severity (`SEVERITY_COLOR`) wrapping the actual blocks, with a plain brand context
  block outside the attachment; severity and diagnostic confidence render in separate blocks on
  purpose, and merchant/provider identifiers are humanised for readability
  (`surfaces/escalation.py:humanize_id`, e.g. `merchant-a` -> `Merchant A`) without changing the
  underlying value. When `citations` is non-empty, a trailing context block lists the tool names
  queried ("Verified against: ..."), so a reader can see the claim was checked without needing the
  raw `query_id`s - those stay in `payload["citations"]` for the dashboard's evidence trail. No
  Slack SDK dependency - one `urllib` POST.
- **Phone** (`surfaces/escalation.py:twilio_provider`, `twiml_for`): places one Twilio
  Programmable Voice call via `urllib` against the Calls REST resource, authenticated with HTTP
  Basic Auth from `CLEARWAVE_TWILIO_ACCOUNT_SID` / `CLEARWAVE_TWILIO_AUTH_TOKEN` /
  `CLEARWAVE_TWILIO_FROM_NUMBER` / `CLEARWAVE_TWILIO_TO_NUMBER`. TwiML is a bounded
  `<Pause length="20"/>` - the call occurring is the signal PRD section 19 asks for; it
  deliberately does not speak, because a trial Twilio account prepends its own "trial account"
  announcement before any TwiML runs regardless. No `twilio` SDK dependency. When credentials are
  incomplete, the call is skipped and recorded as `fallback_dashboard` rather than raising.
  - **Verified against a real Twilio trial account (2026-08-29):** the Calls API rejects the
    inline `Twiml` parameter on trial accounts with HTTP 400 - "trial accounts have limited
    parameter access, upgrade your account to unlock full functionality". Trial calls must
    instead point the `Url` parameter at a Twilio-hosted TwiML Bin containing the exact
    `twiml_for()` string. `twilio_provider` now accepts a `twiml_url` argument (env var
    `CLEARWAVE_TWILIO_TWIML_URL`): when set, the call uses `Url`; when unset, it falls back to
    inline `Twiml`, which only a paid account accepts. Set the Bin URL for the demo, since the
    team is on a trial account.
- **Dashboard**: always `delivered`; the dashboard has no external failure mode of its own.

## Degradation (PRD section 29)

Every channel call is fire-and-forget with a short timeout and swallows its own exception
(`surfaces/escalation.py:escalate` never raises). A failing or unconfigured Slack or phone channel
never blocks the dashboard and never fails the incident; it is recorded as its own outcome and the
other channels still fire.

## Credentials

`CLEARWAVE_SLACK_WEBHOOK_URL`, `CLEARWAVE_SLACK_CHANNEL`, `CLEARWAVE_TWILIO_ACCOUNT_SID`,
`CLEARWAVE_TWILIO_AUTH_TOKEN`, `CLEARWAVE_TWILIO_FROM_NUMBER`, `CLEARWAVE_TWILIO_TO_NUMBER`,
`CLEARWAVE_TWILIO_TWIML_URL` are environment variables only, never committed. None of them appear
in `docs/contracts/` as a cross-workstream boundary field, because they are W4's external
infrastructure, not part of any interface another workstream reads.

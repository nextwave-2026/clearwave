# ClearWave operator manual

This is the bring-up manual, not the judge-facing pitch script. It is for the teammate who owns a fresh demo session and does not need to know the code. After the system is online, use [`demo-sequence.md`](demo-sequence.md) for the pitch.

All displayed merchants, banks, payments and incidents are simulated data from this project. They do not describe a real incident.

## Before step 1: one-time setup

Run these from the repository root. These checks were run on current `main` today:

```sh
pwd -P
git rev-parse --show-toplevel
docker --version
docker compose version
test -f .env || cp .env.example .env
printf 'venv: '; test -x .venv/bin/python || python3 -m venv .venv; .venv/bin/python --version
make install
```

Expected shape: Docker and Compose print versions, `.env: present`, the virtualenv prints Python 3.14.x (or another supported Python), and `make install` ends with `install: dependencies installed from every requirements.txt`.

**`.env` is untracked. A fresh clone or worktree does not have it.** Copy `.env.example` to `.env` before starting anything, then fill in the credentials below. Never commit `.env`.

### Credentials and external services

| Variable | What it enables | If absent or incomplete |
| --- | --- | --- |
| `OPENAI_API_KEY` | Investigation narrative | Detection and dashboard still work; the investigator stores `agent_unavailable` instead of a model diagnosis. |
| `CLEARWAVE_SLACK_WEBHOOK_URL` | Slack for high and critical incidents | The dashboard records Slack as `not_configured`; no Slack message is sent. |
| `CLEARWAVE_TWILIO_ACCOUNT_SID`, `CLEARWAVE_TWILIO_AUTH_TOKEN`, `CLEARWAVE_TWILIO_FROM_NUMBER`, `CLEARWAVE_TWILIO_TO_NUMBER` | Phone for critical incidents | All four are required. Any missing value skips the live call and records `fallback_dashboard`. |
| `CLEARWAVE_TWILIO_TWIML_URL` | Hosted TwiML instruction for the call | A paid Twilio account may use inline TwiML. A Twilio trial rejects inline TwiML with HTTP 400; use a TwiML Bin URL. |

`CLEARWAVE_SLACK_CHANNEL` is optional; when absent the dashboard labels the channel `#control-tower`. `CLEARWAVE_DB` is optional; the default is `state/clearwave.db`. The compose services set their internal Kafka and Schema Registry addresses themselves.

The empty `OPENAI_BASE_URL=` trap was tested today against this checkout: the current loader removes empty OpenAI settings, `OpenAI()` constructed successfully, and `base_url` was not passed. Keep the line absent or populated; do not rely on an empty value with an older checkout or SDK.

### Ports

The normal compose session needs these host ports free:

- `8082`: dashboard, opened at <http://127.0.0.1:8082/>
- `8081`: Schema Registry
- `9092`: Kafka

Check before starting:

```sh
ss -ltn '( sport = :8081 or sport = :8082 or sport = :9092 )'
```

No rows means the ports are free. If a row is present, identify the owner first. Do not stop a process or compose project you did not start. Use a different isolated compose project and explicit port override, or ask the stack owner; do not run `docker compose down` without a project name.

## 1. Start infrastructure and services

On a machine where the `clearwave` project is yours, run:

```sh
make stack-up
```

This prepares healthy history, then starts Kafka, Schema Registry, all three workers, the detector, the investigator and the dashboard. History preparation took **3.7-3.9 seconds** in today's run. Compose startup took **under two minutes** in today's isolated run; allow up to five minutes on a cold Docker machine.

Verify:

```sh
make stack-status
```

Look for `broker: healthy`, `schema-registry: healthy`, all three workers `running`, `detector: running`, `investigation: running`, and `dashboard: answering http://127.0.0.1:8082/api/overview`. Open <http://127.0.0.1:8082/> and confirm the page loads.

**Verification limit:** I did not run bare `make stack-up` today because the `clearwave` project and its containers belonged to another live worker. I verified the same current-main service set in an isolated project with distinct names and ports, and tore down only that project. Treat bare `make stack-up` as unverified on this machine, not as a claim that it was run here.

## 2. Start cohort and payment traffic

There is no second command. `make stack-up` starts `worker-merchant-a`, `worker-merchant-b` and `worker-merchant-c`; they continuously publish healthy payment traffic.

Verify:

```sh
docker compose ps
```

Each worker should show `Up`; Kafka and Schema Registry should show healthy. On the dashboard, the ingestion/provenance area should show accepted events and a recent event timestamp. The prepared history is already warm: today's observed output was `481 buckets / 15628 attempts` for `merchant-b/adyen` and `8.02h / 28860 payments` for merchant-b.

Do not wait six hours. History preparation supplies the baseline in seconds. Allow **about 15-45 seconds** for live workers and the detector to begin showing changing counts.

## 3. Trigger the demo anomaly

Use the dashboard, not a scenario name:

1. Open <http://127.0.0.1:8082/>.
2. Click **Developing deviation** in the masthead.
3. Later, after the watch is visible, click **Collapse**.
4. At the end, click **Clear**.

The control targets merchant-b / adyen and publishes to the worker control topic. It does not restart a worker. The browser status text must say either **You started a developing deviation...**, **You started a collapse...**, or **You cleared the introduced deviation...**. If it says Kafka could not be reached, nothing was injected; do not continue as if it was.

An API check of the same control, verified today against the isolated dashboard, is:

```sh
DASHBOARD_URL=http://127.0.0.1:8082
curl --fail --silent --show-error -X POST "$DASHBOARD_URL/api/trigger" -H 'Content-Type: application/json' -d '{"stage":"developing"}'
```

Expected JSON includes `"delivered": true`, `"fired": true`, `"stage": "developing"`, and a message saying the developing deviation started. Use `{"stage":"collapse"}` and `{"stage":"clear"}` for the other two stages. Do not send these API commands as well as clicking the buttons.

## 4. Start or verify the detector

There is no separate start command: the detector is the compose `detector` service started in step 1. It consumes the three Kafka topics and sweeps the shared SQLite store every 45 seconds.

Verify:

```sh
docker compose ps detector
DASHBOARD_URL=http://127.0.0.1:8082
curl --fail --silent "$DASHBOARD_URL/api/ingestion"
```

The first command should show `detector` `Up`. The second returns JSON containing `accepted`, `stored`, `watermark` and `newest_event_at`; `accepted` and the attempt count should increase while traffic runs. A developing watch may take up to **240 seconds**. A collapse may take up to **480 seconds**; do not call silence a success.

## 5. Start or verify the investigator

There is no separate start command: the investigator daemon is the compose `investigation` service started in step 1. It watches the same `state/clearwave.db` and claims `watching` or `detected` rows.

Verify:

```sh
docker compose ps investigation
```

It should show `investigation` `Up`. The dashboard lifecycle should move through `watching`, then `detected`/`investigating`, then usually `diagnosed`. A watch is investigated but must remain a watch and must not page.

Without `OPENAI_API_KEY`, the expected final result is `agent_unavailable`, not a crash. With a working key, allow **about 45-110 seconds** for the bounded model investigation after detection.

## 6. Start and open the dashboard

The dashboard starts in step 1. Open:

<http://127.0.0.1:8082/>

Verify by seeing the ClearWave page, then refresh once. The compose health check also proves the server is answering `/api/overview` before `make stack-up` returns.

Do not run `make surfaces-serve` alongside the compose dashboard; it is a different host-side server and defaults to another port.

## 7. Verify the investigation result in the dashboard

After **Collapse**, wait for the incident to enter the queue. Then click:

1. **Incident queue**.
2. The row whose lifecycle is `diagnosed` (or `investigating` while it is still working).
3. **Incident detail**.
4. **Evidence trail**.

A successful observed result contains a cohort, change, financial impact, lifecycle state and an investigation outcome. A completed model result also shows a leading hypothesis, competing explanations, confidence, recommended next action and evidence entries with query citations. The live isolated run reached `diagnosed` with `severity: high`; its model was unavailable in that run, so its honest outcome was `agent_unavailable` with no fabricated narrative.

The board must not count a `watching` row as an active incident. A watch can disappear if the measured deviation recovers; that is not a successful incident.

## 8. Verify Slack

Click **Escalation** in the view bar and inspect the incident's Slack row.

- `delivered`: Slack path worked. Also check the configured Slack channel for the ClearWave message.
- `not_configured`: `CLEARWAVE_SLACK_WEBHOOK_URL` is absent; dashboard fallback is working, but no Slack message was sent.
- `failed`: the webhook was reached but rejected or errored; keep the dashboard result and report the failure.

Today's read-only check of the long-running demo showed Slack rows with `status: delivered`. The isolated safety run intentionally had Slack disabled and showed `status: not_configured`; it sent no external message.

## 9. Verify the phone-call path

A phone call is bound only to **critical** severity. Do not manufacture a critical incident or repeatedly test-call during demo hours. The captain's standing position is that the live phone path works; the safe repeatable check is the dry run:

```sh
CLEARWAVE_TWILIO_ACCOUNT_SID=AC12345678901234567890 CLEARWAVE_TWILIO_AUTH_TOKEN=demo-token CLEARWAVE_TWILIO_FROM_NUMBER=+15550000001 CLEARWAVE_TWILIO_TO_NUMBER=+15550000002 CLEARWAVE_TWILIO_TWIML_URL=https://example.invalid/twiml .venv/bin/python scripts/check_phone_channel.py --dry-run
```

Expected ending: `instruction parameter: Url (TwiML Bin)` and `Dry run: no call placed.` This proves request construction only; the values above are placeholders and must not be used for a live call.

For a real critical incident, inspect **Escalation**. `phone: delivered` means Twilio accepted the call. `fallback_dashboard` means one or more Twilio variables were missing. `failed` means the request was attempted and failed; the dashboard remains available. Do not chase a 403 from an isolated verification run.

## 10. Reset and run again

Use this only for a project you own. The clean reset is:

```sh
DASHBOARD_URL=http://127.0.0.1:8082
curl --fail --silent --show-error -X POST "$DASHBOARD_URL/api/trigger" -H 'Content-Type: application/json' -d '{"stage":"clear"}'
docker compose -p clearwave down --volumes --remove-orphans
make stack-up
make stack-status
```

The first command must return `"delivered": true` and `"stage": "clear"`. `docker compose ... down` must name your project. `make stack-up` then replaces the store and rewrites warm healthy history; it does not pre-create an incident. The expected clean check is `active_incident_count=0` and no watches, followed by the wait and clicks in steps 2-3.

Today's reset was verified on the isolated project with its own project name, and only that project's containers, network and volume were removed. I did not execute the literal `-p clearwave` reset here because that project name was shared; use it only when you have confirmed that you own that project. The shared `clearwave`, `clearwave-verify-demo` and `clearwave-fallback-demo` projects were not stopped, restarted or cleaned up.

For the pitch itself, stop here and use [`demo-sequence.md`](demo-sequence.md). It is a separate judge-facing rehearsal script and does not replace this bring-up manual.

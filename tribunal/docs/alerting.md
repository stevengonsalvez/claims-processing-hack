# Proactive alerting (Challenge 3)

Every adjudication ends in `build_verdict`. Right after it, `record_verdict` (in
`tribunal/telemetry.py`) stamps the decision on the live `tribunal.adjudicate` span
**and** emits a `tribunal.verdict` log record through the Azure Monitor OpenTelemetry
exporter. An Azure Monitor scheduled-query alert watches that stream and emails a human
whenever the tribunal flags a likely fraudulent claim, without anyone watching the UI.

```
┌──────────────┐  record_verdict  ┌───────────────┐  OTel log  ┌────────────┐
│ workflow.py  │─────────────────▶│ telemetry.py  │───────────▶│ AppTraces  │
│ build_verdict│  span attrs +    │ claims-tribunal│  exporter  │ (LA ws)    │
└──────────────┘  log record      └───────────────┘            └─────┬──────┘
                                                                     │ every 5m
                                                    ┌────────────────▼───────────────┐
                                                    │ tribunal-fraud-referrals       │
                                                    │ fraud_score > 0.6, 15m window  │
                                                    └────────────────┬───────────────┘
                                                                     ▼
                                                        ┌────────────────────────┐
                                                        │ action group           │
                                                        │ tribunal-alerts (email)│
                                                        └────────────────────────┘
```

## What is emitted

`record_verdict(verdict)` writes, on the current span and as log-record properties:

| field | example | source |
|---|---|---|
| `claim.id` | `CLM-20260907-911F18` | verdict claim id |
| `tribunal.decision` | `refer` | Arbiter decision |
| `tribunal.fraud_score` | `0.8` | Fraud Investigator score |
| `tribunal.net_payout` | `0` | payout after deductible / limit |

The log message itself is `tribunal.verdict <claim_id> <decision> fraud=<x.xx> net=<n>`,
logger `claims-tribunal` at WARNING, which is the logger `configure_azure_monitor(...)`
is bound to. It lands in the workspace table **`AppTraces`**, fields under `Properties`.
The call in `workflow.py` is wrapped in try/except: telemetry never breaks an adjudication.

## What fires

`tribunal-fraud-referrals`, a scheduled-query rule scoped to the Log Analytics workspace
`msagthack-loganalytics-6ahymubsyajs6` (rg-labuser-0009):

- severity **2**, evaluated every **5 minutes** over a **15-minute** window
- fires when the row count is **> 0** (i.e. at least one high-fraud verdict)
- notifies action group `tribunal-alerts` → email `steven.gonsalvez@gmail.com`

## The KQL

```kusto
AppTraces
| where Message has "tribunal.verdict" or tostring(Properties["event_name"]) == "tribunal.verdict"
| extend claim_id = tostring(Properties["claim.id"]),
         decision = tostring(Properties["tribunal.decision"]),
         fraud_score = todouble(Properties["tribunal.fraud_score"]),
         net_payout = todouble(Properties["tribunal.net_payout"])
| where fraud_score > 0.6
| project TimeGenerated, claim_id, decision, fraud_score, net_payout
```

Run it ad hoc (drop the `fraud_score` filter to see every verdict):

```bash
az monitor log-analytics query -w 550c4b6a-c757-4d54-a389-b45c26ecc517 \
  --analytics-query 'AppTraces | where TimeGenerated > ago(60m) | where Message has "tribunal.verdict"
  | extend claim_id=tostring(Properties["claim.id"]), decision=tostring(Properties["tribunal.decision"]),
           fraud_score=todouble(Properties["tribunal.fraud_score"]), net_payout=todouble(Properties["tribunal.net_payout"])
  | project TimeGenerated, claim_id, decision, fraud_score, net_payout | order by TimeGenerated asc' -o table
```

## Create / update the alert

```bash
tribunal/alerts.sh          # idempotent: upserts the action group, creates or updates the rule
```

Overridable env vars: `TRIBUNAL_RG`, `TRIBUNAL_WORKSPACE`, `TRIBUNAL_ACTION_GROUP`,
`TRIBUNAL_ALERT`, `TRIBUNAL_ALERT_EMAIL`. The script prints the rule back at the end;
`az monitor scheduled-query show -g rg-labuser-0009 -n tribunal-fraud-referrals` shows it later.

## Seeing it in the portal

1. **Rule and its query** — portal → Monitor → Alerts → Alert rules → `tribunal-fraud-referrals`
   (or resource group `rg-labuser-0009` → the rule). "Edit" shows the KQL, window and severity.
2. **Fired alerts** — Monitor → Alerts, filter resource group `rg-labuser-0009`. A fired
   instance carries the matching rows, so the referred claim id is visible in the alert detail.
3. **Raw events** — Application Insights `msagthack-appinsights-6ahymubsyajs6` → Logs → paste
   the KQL above. The same `claim.id` joins straight to the `tribunal.adjudicate` trace
   (Transaction search) with its six `agent *` child spans.
4. **Email** — the action group `tribunal-alerts` sends to steven.gonsalvez@gmail.com; the
   address must be confirmed once from Azure's verification mail before delivery starts.

## Demo path

```bash
curl -s -N -m 240 -F sample=crash4 localhost:8423/adjudicate > logs/alert-trigger.log
```

crash4 is the planted-fraud claim (CLM-0412, same VIN paid 3 months ago): the Fraud
Investigator returns 0.8, the Arbiter refers, and within one evaluation cycle the rule
fires. Ingestion into `AppTraces` takes roughly 1-3 minutes, so allow ~5 minutes end to end.

## Observed once, end to end (2026-09-07)

`crash4` → `CLM-20260907-911F18 refer fraud=0.80 net=0` in `AppTraces` at 11:20:30Z;
`tribunal-fraud-referrals` moved to **Fired**, Sev2, at 11:25:14Z (one evaluation cycle
later) against the workspace. Raw outputs: `logs/alert-kql.log`, `logs/alert-rule-show.json`,
`logs/alert-fired.json`, `logs/alerts-create.log`.

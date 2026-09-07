#!/usr/bin/env bash
# Challenge 3, proactive alerting. Idempotent: safe to re-run.
#
# Creates (or updates) an action group that emails a human, and a scheduled-query
# alert over the Log Analytics workspace behind Application Insights that fires when
# the tribunal emits a `tribunal.verdict` event with fraud_score > 0.6 in the last
# 15 minutes. The event is written by tribunal/telemetry.py:record_verdict().
set -euo pipefail

RG="${TRIBUNAL_RG:-rg-labuser-0009}"
WORKSPACE="${TRIBUNAL_WORKSPACE:-msagthack-loganalytics-6ahymubsyajs6}"
ACTION_GROUP="${TRIBUNAL_ACTION_GROUP:-tribunal-alerts}"
ALERT_NAME="${TRIBUNAL_ALERT:-tribunal-fraud-referrals}"
ALERT_EMAIL="${TRIBUNAL_ALERT_EMAIL:-steven.gonsalvez@gmail.com}"

read -r -d '' KQL <<'KQLEOF' || true
AppTraces
| where Message has "tribunal.verdict" or tostring(Properties["event_name"]) == "tribunal.verdict"
| extend claim_id = tostring(Properties["claim.id"]),
         decision = tostring(Properties["tribunal.decision"]),
         fraud_score = todouble(Properties["tribunal.fraud_score"]),
         net_payout = todouble(Properties["tribunal.net_payout"])
| where fraud_score > 0.6
| project TimeGenerated, claim_id, decision, fraud_score, net_payout
KQLEOF

WS_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$WORKSPACE" --query id -o tsv)

echo "==> action group $ACTION_GROUP ($ALERT_EMAIL)"
AG_ID=$(az monitor action-group create \
  -g "$RG" -n "$ACTION_GROUP" --short-name tribunal \
  --action email adjuster "$ALERT_EMAIL" \
  --query id -o tsv)

echo "==> scheduled query alert $ALERT_NAME"
if az monitor scheduled-query show -g "$RG" -n "$ALERT_NAME" >/dev/null 2>&1; then
  az monitor scheduled-query update -g "$RG" -n "$ALERT_NAME" \
    --condition "count 'tribunal_fraud' > 0" \
    --condition-query tribunal_fraud="$KQL" \
    --evaluation-frequency 5m --window-size 15m --severity 2 \
    --action-groups "$AG_ID" -o none
else
  az monitor scheduled-query create -g "$RG" -n "$ALERT_NAME" \
    --scopes "$WS_ID" \
    --description "Tribunal referred a claim with fraud_score > 0.6 in the last 15 minutes" \
    --condition "count 'tribunal_fraud' > 0" \
    --condition-query tribunal_fraud="$KQL" \
    --evaluation-frequency 5m --window-size 15m --severity 2 \
    --action-groups "$AG_ID" -o none
fi

az monitor scheduled-query show -g "$RG" -n "$ALERT_NAME" \
  --query "{name:name, enabled:enabled, severity:severity, window:windowSize, frequency:evaluationFrequency, scopes:scopes, actions:actions.actionGroups}" -o json

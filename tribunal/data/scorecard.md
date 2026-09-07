# Tribunal scorecard (5/5 policy decisions match ground truth)

| claim | ground truth | policy agent | match | tribunal verdict | fraud | net payout | s |
|---|---|---|---|---|---|---|---|
| crash1 | DENIED | DENIED | yes | DENIED | 0.10 | $0 | 42.6 |
| crash2 | APPROVED | APPROVED | yes | APPROVED | 0.00 | $9,950 | 40.6 |
| crash3 | DENIED | DENIED | yes | DENIED | 0.00 | $0 | 41.0 |
| crash4 | APPROVED | APPROVED | yes | REFER | 0.80 | $4,900 | 33.0 |
| crash5 | APPROVED | APPROVED | yes | REFER | 0.00 | $0 | 34.2 |

Tribunal verdict may legitimately differ from coverage ground truth: REFER is raised when the Fraud Investigator finds planted prior-claim evidence (crash4 VIN duplicate, crash3 repeat claimant).

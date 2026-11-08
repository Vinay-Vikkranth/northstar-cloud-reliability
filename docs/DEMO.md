# Five-minute demo

The app is labeled Synthetic demo. Numbers below are the scripted simulator values, not a customer result.

1. Start with `scripts/start.ps1` and open http://127.0.0.1:8100.
2. Sign in as `northstar.admin` (password in `var/local-credentials.txt`). Open Demo controls and choose Reset demo workspace. That sets Alex Rivera's CRM plan to premium and the support plan to basic. It does not call HubSpot.
3. Sign out. Sign in as `northstar.operator`. Open the Alex Rivera incident. Choose Investigate and wait until the diagnosis says `missing_update` and the next step is `propose_repair`. The diff is basic to premium. The hash is the exact change.
4. Sign out. Sign in as `northstar.approver`. Open the same incident, read the diff, and approve. Wait until the page says the repair was accepted, the fresh read verified it, and the incident is resolved. Those three lines are separate.
5. Lost response, without a second write: the support admin fault `drop_response_after_commit` stores a success receipt and returns 500. Replaying the same idempotency key returns the receipt and does not increase `write_count` again. The automated test `test_support_idempotency_conflict_and_lost_response` is the evidence. The browser demo does not click that fault.

`northstar.combined` can do both steps. Say that this is a labeled compromise, then show the split users above. `harbor.operator` cannot reset the demo.

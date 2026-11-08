# Integrations

## Synthetic services

The CRM simulator and the support service are separate HTTP processes. Public reads use bearer tokens from the environment. Fault injection is only on `/admin/*` with the admin token. Those tokens in `.env.example` are local demo values.

Demo reset calls the admin endpoints for the simulators and only when the workspace slug is `northstar-demo` and the workspace is synthetic.

## HubSpot

Checked against HubSpot's CRM v3 contact documentation on 2026-11-02.

- Read: `GET /crm/v3/objects/contacts/{id}?properties={plan_property},lastmodifieddate`
- Auth: `Authorization: Bearer` a private-app or static token
- Scope: `crm.objects.contacts.read`
- Page size max 100, cursor `paging.next.after`
- 429 uses `Retry-After`
- The client refuses any origin other than `https://api.hubapi.com` unless a test passes a mock transport

Verified November 4, 2026: free developer test account **Northstar Recovery Test** (REDACTED_HUBSPOT_PORTAL_ID), fictional contact **Alex Recovery Demo** (REDACTED_HUBSPOT_CONTACT_ID), property `northstar_plan=premium`. The approved Service Key has only `crm.objects.contacts.read`; it is saved privately in the ignored `.env`. Live reads and the full approved local-support repair passed. The app cannot write HubSpot with this key. No card, purchase, paid upgrade or hosted-model API was used. Developer test accounts can expire after inactivity; check their status before a later demonstration. Automatically offered trial features are not needed by this integration.

To reproduce the setup in another free test account (the current machine is already configured). Do not paste a key into chat.

1. Create or open a free HubSpot developer test account. Do not use a customer portal.
2. Open Development, then Keys, then Service keys. A super admin or a user with Developer tools access can create one. Service keys are the current REST credential. A legacy private-app token also works if the account still has one. This project does not need webhooks.
3. Add only the scope `crm.objects.contacts.read`, then create the key. Leave it in the HubSpot UI.
4. Create one fictional contact. Note its numeric contact id.
5. Create the plan property yourself in the HubSpot UI. This repository will not create properties. Note the property's internal name.
6. Put the key, contact id, and property name in this project's `.env` as `HUBSPOT_TOKEN`, `HUBSPOT_CONTACT_ID`, and `HUBSPOT_PLAN_PROPERTY`. Do not commit that file.
7. Run `python scripts/hubspot_smoke.py`. For a fictional Premium contact, `python scripts/prepare_hubspot_workspace.py --confirm-fictional-contact CONTACT_ID` prepares the isolated local fixture. It does not reset existing records or write HubSpot. The browser flow then requires a separate approver before updating the local support account.

## Live model

The adapter is `OpenAICompatibleCompleter` in `apps/api/recovery_api/llm.py`. It POSTs to `{CIR_LLM_BASE_URL}/chat/completions`.

A free local choice is a working Ollama server that is already installed. This project does not download a model.

```text
CIR_INVESTIGATOR_MODE=live
CIR_LLM_BASE_URL=http://127.0.0.1:11434/v1
CIR_LLM_MODEL=llama3:latest
CIR_LLM_API_KEY=
CIR_COST_POLICY=local_unmeasured
```

Leave `CIR_LLM_API_KEY` empty for a local server that does not require one. `local_unmeasured` is required for a server that does not send `cost_usd`. Monetary cost is then labeled unmeasured. It is not stored as zero. Tool-call, token, response-size, and elapsed-time limits still stop the loop. The same variable applies to `python evaluations/run_baseline.py --live`.

Verified November 4: the official installer had a valid Ollama Inc signature and repaired the unreadable program. Ollama 0.40.13 is running on port 11434; existing model files were preserved. No model download was needed. The project uses the installed `llama3:latest`, with no model API key. If the service is unavailable after a restart, launch Ollama, then run `python scripts/check_setup.py`.

`CIR_COST_POLICY=strict` is the default. A missing price stops the investigation with `cost_unknown`. `CIR_COST_POLICY=estimated` uses `CIR_INPUT_USD_PER_MILLION` and `CIR_OUTPUT_USD_PER_MILLION` to record a labeled estimate, then compares that estimate with `CIR_MAX_ESTIMATED_COST_USD`. A provider-supplied `cost_usd` is recorded separately as a provider charge. Rates that are missing do not become a zero charge.

Real local inference, raw model evaluation and two browser walkthroughs ran. Raw model accuracy was poor; see `docs/VALIDATION.md`. The application now collects the required account comparison and sync history before asking the model, constrains additional tools to the incident, and computes the repair from policy-checked source evidence. The model does not choose the final write value or approve it. Invalid responses/budget exhaustion escalate instead of authorizing a write. A successful browser run demonstrates integration, not general model reliability.

Hosted free tiers are optional and still count as live only after a real response. Use `estimated` or `strict` for those. Do not use `local_unmeasured` for a hosted bill.

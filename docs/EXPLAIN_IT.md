# Explain and modify the project

This is a practice guide, not a claim that a candidate has independently implemented or mastered the code.

## Five design decisions to explain

1. **One durable store:** jobs, proposals, approvals and repair intents already lived in PostgreSQL. Strengthening that queue makes the extension auditable without adding Temporal only for a keyword. What extra requirements would justify a dedicated workflow engine?
2. **Fencing versus idempotency:** an expired job claim cannot renew or finish with its old token. A guard blocks starting a request after known lease loss. It cannot recall a request already in flight. Which exact destination constraints prevent another side effect?
3. **Approval is data, not a screen:** a proposal binds the intended plan, identities, revision preconditions and expiry. A service restart does not add permission. What happens when evidence changes after approval?
4. **Retries are bounded:** repeated queue replay does not reset the original repair budget. Why would blindly creating a new job risk endless retries?
5. **Observations have denominators:** the terminal-job ratio is not HTTP availability; task execution duration excludes human approval wait; maintenance jobs are excluded. Why is ten samples insufficient for a production reliability claim even though the demo displays an objective?

## Hands-on checks

- Use the separate operator and approver to resolve a fresh synthetic mismatch. Find the receipt and show one write.
- Reject a proposal, then try an old approval request. Explain why it must fail.
- Run `python scripts/native.py test` and read the actual process-termination and stale-token tests.
- Copy a trace ID from Operations and find matching API, worker and connector records in ignored local logs/traces.
- Use the release tool to stage two versions, activate each, roll back and verify the manifest. Explain why that is not a database downgrade or proof of a cloud deployment.
- Change a synthetic mapping in a new branch, add a behavior test and explain the observed effect. Do not change real HubSpot data.

## Truthful interview phrasing

Describe this as an AI-assisted extension of an existing portfolio application. Name the inherited capabilities separately from the new reliability work. Quote actual test/benchmark scope and limitations. Cloud configuration is not the same as an exercised cloud deployment. A script that impersonates an operator is not a measured human labor baseline.

"""Run inside an isolated API container to verify the production Compose stack.

Uses only the fresh fictional fixture, never resets it, and never prints secrets.
Internal HTTP explicitly forwards Secure cookies to emulate the TLS proxy hop;
the service's Secure-cookie configuration is never disabled.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import httpx

BASE = "http://127.0.0.1:8101"
ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def checked(response):
    if response.status_code >= 400:
        # Do not include bodies, cookies, authentication headers or credentials.
        raise RuntimeError(f"Smoke request {response.request.method} {response.request.url.path} returned {response.status_code}")
    return response


def login(record, role, origin):
    client = httpx.Client(base_url=BASE, timeout=10, trust_env=False, follow_redirects=False)
    response = checked(client.post("/api/v1/auth/login", json={"username":record["users"][role], "password":record["passwords"][role]}))
    cookies = response.headers.get_list("set-cookie")
    session = next((value for value in cookies if value.startswith("cir_session=")), "")
    csrf = next((value for value in cookies if value.startswith("cir_csrf=")), "")
    require("; secure" in session.lower() and "; secure" in csrf.lower(), "Production session and CSRF cookies must both have Secure")
    require("; httponly" in session.lower(), "Session cookie must be HttpOnly")
    require("samesite=lax" in session.lower(), "Session cookie must have the expected SameSite policy")
    body = response.json()
    workspace = next((item for item in body["workspaces"] if item["slug"] == "northstar-demo" and item["synthetic"]), None)
    require(workspace is not None, "Expected the isolated synthetic workspace")
    # An ordinary browser sends these Secure cookies over external HTTPS. Caddy
    # forwards them over this private HTTP hop. Simulate only that internal hop.
    forwarded_cookies = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
    client.headers.update({"Cookie":forwarded_cookies, "X-CSRF-Token":body["csrf_token"],
                           "X-Workspace-Id":workspace["id"], "Origin":origin, "X-Forwarded-Proto":"https"})
    return client


def wait_for(client, path, predicate, timeout=90):
    deadline = time.monotonic() + timeout
    state = "unknown"
    while time.monotonic() < deadline:
        value = checked(client.get(path)).json()
        state = value.get("state", "unknown")
        if predicate(value):
            return value
        if state in {"ESCALATED", "NEEDS_INFORMATION"}:
            raise RuntimeError("Synthetic workflow stopped in " + state)
        time.sleep(.2)
    raise RuntimeError("Synthetic workflow timed out in " + state)


def main():
    require(hasattr(os, "getuid") and os.getuid() == 10001, "Run this smoke test inside the nonroot production API container")
    require(os.environ.get("CIR_ENVIRONMENT") == "production", "Production environment is required")
    require(os.environ.get("CIR_COOKIE_SECURE", "").lower() == "true", "Secure cookies must remain enabled")
    origin = os.environ["CIR_CORS_ORIGINS"].split(",")[0]
    require(origin.startswith("https://"), "The configured browser origin must use HTTPS")
    record = json.loads((ROOT/"var"/"local-credentials.json").read_text())
    require(record["users"]["operator"] != record["users"]["approver"], "Operator and approver identities must differ")
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as public:
        checked(public.get(BASE + "/health/ready"))
        page = checked(public.get(BASE + "/"))
        require("<html" in page.text.lower(), "Compiled web UI did not load")
        disabled = (("combined_operator_and_approver", "combined"), ("harbor_operator", "harbor"))
        for user_key, password_key in disabled:
            denied = public.post(BASE + "/api/v1/auth/login", json={"username":record["users"][user_key],"password":record["passwords"][password_key]})
            require(denied.status_code == 401, "A production-disabled convenience account could sign in")

    operator, approver = login(record, "operator", origin), login(record, "approver", origin)
    try:
        metadata = checked(operator.get("/api/v1/meta")).json()
        require(metadata.get("synthetic_demo") is True and metadata.get("source_mode") == "synthetic_crm", "Refusing to operate on a non-synthetic or external-CRM workspace")
        crm = os.environ["CIR_CRM_BASE_URL"]
        support = os.environ["CIR_SUPPORT_BASE_URL"]
        require(urlsplit(crm).hostname == "crm" and urlsplit(support).hostname == "support", "Only the Compose internal fictional connectors are permitted")
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as internal:
            source = checked(internal.get(crm + "/v1/customers/cus_alex", headers={"Authorization":"Bearer " + os.environ["CRM_READ_TOKEN"]})).json()
            def destination():
                return checked(internal.get(support + "/v1/accounts/sup_alex", headers={"Authorization":"Bearer " + os.environ["SUPPORT_TOKEN"]})).json()
            before = destination()
            checked(operator.post("/api/v1/reconcile"))
            items = checked(operator.get("/api/v1/incidents")).json()["items"]
            selected = [item for item in items if "alex" in item.get("account", "").lower()]
            require(len(selected) == 1, "Expected one fictional Alex plan-mismatch incident")
            path = "/api/v1/incidents/" + selected[0]["id"]
            detail = checked(operator.get(path)).json()
            fresh = detail["state"] != "RESOLVED"
            if fresh:
                require(before["plan"] == "basic" and before["write_count"] == 0, "Fixture was already modified; refusing to reset or overwrite it")
                checked(operator.post(path + "/investigate"))
                detail = wait_for(operator, path, lambda body: body.get("proposal") is not None)
                proposal = detail["proposal"]
                approval_path = "/api/v1/proposals/" + proposal["id"] + "/approve"
                approval_body = {"proposal_hash":proposal["proposal_hash"], "reason":"Independent production-container smoke approver reviewed the synthetic plan and revisions."}
                denied = operator.post(approval_path, json=approval_body)
                require(denied.status_code == 403, "Operator was incorrectly allowed to approve a repair")
                checked(approver.post(approval_path, json=approval_body))
                detail = wait_for(operator, path, lambda body: body.get("state") == "RESOLVED")
                repeated = approver.post(approval_path, json=approval_body)
                require(repeated.status_code == 409, "Repeated approval should be rejected")
            require(detail.get("repair_state") == "VERIFIED", "Repair did not reach VERIFIED")
            require((detail.get("verification") or {}).get("outcome") == "verified", "Fresh verification did not succeed")
            observed = destination()
            require(source["plan"] == "premium" and observed["plan"] == "premium", "The fictional plan was not reconciled")
            require(observed["write_count"] == 1, "Expected exactly one destination side effect")
            source_after = checked(internal.get(crm + "/v1/customers/cus_alex", headers={"Authorization":"Bearer " + os.environ["CRM_READ_TOKEN"]})).json()
            require(source_after["revision"] == source["revision"], "The read-only CRM source changed during the smoke test")
        checked(operator.get("/api/v1/operations"))
        report = {"checked_at":datetime.now(timezone.utc).isoformat(), "scope":"isolated production Compose container fixture",
                  "nonroot_uid":os.getuid(), "secure_session_cookies":True, "compiled_ui":True,
                  "convenience_accounts_disabled":True, "distinct_operator_and_approver":True,
                  "fresh_workflow_executed":fresh, "incident_state":"RESOLVED", "repair_state":"VERIFIED",
                  "destination_write_count":1, "crm_source_unchanged":True, "authenticated_operations":True,
                  "public_tls_or_cloud_provider_verified":False}
        (ROOT/"var"/"production-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        operator.close()
        approver.close()


if __name__ == "__main__":
    main()

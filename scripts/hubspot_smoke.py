"""Read one configured HubSpot contact. Exits 2 when the free test account is not configured."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "packages" / "recovery_core")]

from recovery_api.hubspot import HubSpotClient, HubSpotError
from recovery_api.settings import get_settings


def main() -> int:
    settings = get_settings()
    if not settings.hubspot_token or not settings.hubspot_contact_id or not settings.hubspot_plan_property:
        print("BLOCKED: set HUBSPOT_TOKEN, HUBSPOT_CONTACT_ID, and HUBSPOT_PLAN_PROPERTY. Do not create the property from this script.")
        return 2
    client = HubSpotClient(token=settings.hubspot_token, plan_property=settings.hubspot_plan_property)
    try:
        record = client.get_contact(settings.hubspot_contact_id)
    except HubSpotError as exc:
        print(f"hubspot_error={exc}")
        return 1
    finally:
        client.close()
    print(f"status={record.get('status')} plan_present={bool(record.get('plan'))} freshness={record.get('freshness')}")
    return 0 if record.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

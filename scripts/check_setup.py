"""Read-only setup report. Does not invoke models or reveal credentials."""
from pathlib import Path
import sys
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
from recovery_api.settings import Settings


def main():
    settings = Settings(_env_file=ROOT / ".env")
    print("Northstar setup check (configuration is not live verification)")
    for name, url in (
        ("Website", "http://127.0.0.1:8100/"),
        ("API", "http://127.0.0.1:28601/health/ready"),
        ("CRM simulator", "http://127.0.0.1:28611/health/ready"),
        ("Support service", "http://127.0.0.1:28621/health/ready"),
        ("Ollama", "http://127.0.0.1:11434/api/tags"),
    ):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                print(f"{name}: {'reachable' if response.status == 200 else 'not ready'}")
        except (urllib.error.URLError, OSError):
            print(f"{name}: unavailable")
    print(f"Investigator mode: {settings.cir_investigator_mode}")
    print("Model endpoint and name configured:", bool(settings.cir_llm_base_url and settings.cir_llm_model))
    print("HubSpot test-contact settings complete:", bool(settings.hubspot_token and settings.hubspot_contact_id and settings.hubspot_plan_property))
    print("No model inference or HubSpot requests were made. No secrets were printed.")


if __name__ == "__main__":
    main()

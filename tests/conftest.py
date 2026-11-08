"""Ordinary tests never inherit live model or CRM credentials from a local .env.

Live checks are separate opt-in scripts and browser scenarios.
"""
import os

for key, value in {
    "CIR_INVESTIGATOR_MODE": "deterministic",
    "CIR_LLM_BASE_URL": "",
    "CIR_LLM_MODEL": "",
    "CIR_LLM_API_KEY": "",
    "HUBSPOT_TOKEN": "",
    "HUBSPOT_CONTACT_ID": "",
    "CIR_COST_POLICY": "strict",
}.items():
    os.environ[key] = value

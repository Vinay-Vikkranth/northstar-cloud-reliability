$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (Test-Path ".venv/Scripts/python.exe") { & .venv/Scripts/python.exe -B scripts/native.py serve } else { python -B scripts/native.py serve }

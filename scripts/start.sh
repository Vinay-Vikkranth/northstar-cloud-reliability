#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
exec "${PYTHON:-python}" -B scripts/native.py serve

"""Observable build marker. The health response and worker log both expose it."""

import os
BUILD_ID = os.environ.get("NORTHSTAR_RELEASE", "2026.11.08.1")

"""Global constants and configurations for the TydomIo library."""

import os

DEV_MODE = (
    os.environ.get("DEV_MODE", "0") == "1"
)  # Set to True to save unknown responses and request to files for debugging

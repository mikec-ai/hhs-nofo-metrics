"""Runtime package identity from installed distribution metadata."""

from __future__ import annotations

from importlib import metadata
from typing import Final

PACKAGE_NAME: Final = "hhs-nofo-metrics"
SOURCE_FALLBACK_VERSION: Final = "0.5.1"


def package_version() -> str:
    """Return the installed distribution version or the source-tree fallback."""

    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return SOURCE_FALLBACK_VERSION


PACKAGE_VERSION: Final = package_version()

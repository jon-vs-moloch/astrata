"""Local model registry surfaces."""

from astrata.local.models.discovery import discover_local_models
from astrata.local.models.registry import LocalModelRecord, LocalModelRegistry

__all__ = ["LocalModelRecord", "LocalModelRegistry", "discover_local_models"]

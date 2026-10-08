"""Versioned local HTTP API consumed by the Avalonia desktop client."""

from .app import create_app

__all__ = ["create_app"]

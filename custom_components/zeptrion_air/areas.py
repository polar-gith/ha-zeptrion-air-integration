"""Rooms stored in the zApp (channel descriptor "group"), suggested as areas."""

from __future__ import annotations

from typing import Any


def suggested_area_for_hub(channels: list[dict[str, Any]]) -> str | None:
    """The room of the zApp: the group all its channels share, if any."""
    groups = {str(c.get("api_group") or "").strip() for c in channels}
    groups.discard("")
    return groups.pop() if len(groups) == 1 else None


def suggested_area_for_channel(channel: dict[str, Any]) -> str | None:
    """The room of one channel as stored in the zApp (chdes group)."""
    group = str(channel.get("api_group") or "").strip()
    return group or None

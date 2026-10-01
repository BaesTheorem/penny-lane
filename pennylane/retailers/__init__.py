"""Retailer lanes. `registry()` returns one live adapter per enabled lane."""

from __future__ import annotations

from .base import Retailer

_cache: dict[str, Retailer] = {}


def registry() -> dict[str, Retailer]:
    if not _cache:
        from .dollargeneral import DollarGeneral
        from .homedepot import HomeDepot
        from .lowes import Lowes
        from .walmart import Walmart
        for cls in (HomeDepot, Lowes, DollarGeneral, Walmart):
            _cache[cls.key] = cls()
    return _cache

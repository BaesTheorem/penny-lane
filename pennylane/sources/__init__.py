"""Community penny-list sources. `all_sources()` returns one instance each."""

from __future__ import annotations

from .base import Source


def all_sources() -> list[Source]:
    from .pennycentral import PennyCentral
    from .pennyrecon import PennyRecon
    from .rebeldealz import RebelDealz
    from .wordpress import all_sites
    return [PennyCentral(), PennyRecon(), *all_sites(), RebelDealz()]

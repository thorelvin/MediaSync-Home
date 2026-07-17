from __future__ import annotations


class MutationPermit:
    __slots__ = ()

    def __new__(cls) -> "MutationPermit":
        raise TypeError("MutationPermit instances are issued only by a live lease adapter")


"""Authority chain helpers for constitutional control."""

from __future__ import annotations

from pydantic import BaseModel


class AuthorityChain(BaseModel):
    source: str = "user"
    delegated_via: str = "constitution"

    @property
    def summary(self) -> str:
        return f"{self.source} -> {self.delegated_via}"

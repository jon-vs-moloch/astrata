"""Authority chain helpers for constitutional control."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field

from astrata.governance.constitution import load_constitution_text
from astrata.governance.documents import load_governance_bundle


class AuthorityLevel(Enum):
    """Enumeration of authority levels in the governance system."""

    USER = 1
    SYSTEM = 2
    ADMIN = 3
    CONSTITUTIONAL = 4


class AuthorityChain(BaseModel):
    """Represents a chain of authority delegation for constitutional control."""

    source: str = Field(default="user", description="The origin of the authority")
    delegated_via: str = Field(default="constitution", description="The mechanism of delegation")
    chain: List[str] = Field(default_factory=list, description="Additional delegation steps")

    @property
    def level(self) -> AuthorityLevel:
        """Determine the authority level based on the source."""
        source_lower = self.source.lower()
        if source_lower == "user":
            return AuthorityLevel.USER
        elif source_lower == "system":
            return AuthorityLevel.SYSTEM
        elif source_lower == "admin":
            return AuthorityLevel.ADMIN
        elif "constitution" in self.delegated_via.lower() or any(
            "constitution" in item.lower() for item in self.chain
        ):
            return AuthorityLevel.CONSTITUTIONAL
        else:
            return AuthorityLevel.USER  # default

    @property
    def summary(self) -> str:
        """Generate a human-readable summary of the authority chain."""
        base = f"{self.source} -> {self.delegated_via}"
        if self.chain:
            base += " -> " + " -> ".join(self.chain)
        return base

    def extend(self, new_via: str) -> AuthorityChain:
        """Create a new authority chain with an additional delegation step."""
        new_chain = self.chain.copy()
        new_chain.append(new_via)
        return AuthorityChain(source=self.source, delegated_via=self.delegated_via, chain=new_chain)

    def validate_against_constitution(self, constitution_text: str) -> bool:
        """Validate if the authority chain references constitutional authority."""
        if "constitution" in self.delegated_via.lower():
            return True
        for item in self.chain:
            if "constitution" in item.lower():
                return True
        # Could add more sophisticated validation here based on constitution content
        return False

    def validate_against_governance_bundle(self, bundle) -> bool:
        """Validate if the authority chain is valid against the governance bundle."""
        # Check constitution
        if self.validate_against_constitution(bundle.constitution.content):
            return True
        # Check planning docs for references to authority or governance
        for doc_name, doc in bundle.planning_docs.items():
            if doc.exists and any(
                term in doc.content.lower() for term in ["authority", "constitution", "governance"]
            ):
                if any(
                    term in self.delegated_via.lower() or term in " ".join(self.chain).lower()
                    for term in [doc_name.replace("-", "_"), "planning"]
                ):
                    return True
        return False

    def is_authorized(self, project_root: Path) -> bool:
        """Check if the authority chain is valid against the loaded constitution."""
        constitution = load_constitution_text(project_root)
        return self.validate_against_constitution(constitution)

    def is_authorized_full(self, project_root: Path) -> bool:
        """Check if the authority chain is valid against the full governance bundle."""
        bundle = load_governance_bundle(project_root)
        return self.validate_against_governance_bundle(bundle)

    def validate_level(self, required_level: AuthorityLevel) -> bool:
        """Check if this authority chain meets or exceeds the required authority level."""
        return self.level.value >= required_level.value


def create_user_authority() -> AuthorityChain:
    """Create a basic authority chain originating from the user."""
    return AuthorityChain()


def create_system_authority(delegated_via: str = "constitution") -> AuthorityChain:
    """Create an authority chain for system-level operations."""
    return AuthorityChain(source="system", delegated_via=delegated_via)


def create_admin_authority() -> AuthorityChain:
    """Create an authority chain for administrative operations."""
    return AuthorityChain(source="admin", delegated_via="constitution")


def create_constitutional_authority(section: str = "general") -> AuthorityChain:
    """Create an authority chain directly from constitutional authority."""
    return AuthorityChain(source="constitution", delegated_via=f"section_{section}")


def create_prime_authority() -> AuthorityChain:
    """Create an authority chain for Prime (top-level coordinating intelligence)."""
    return AuthorityChain(source="prime", delegated_via="constitution")


def create_assistant_authority(delegated_via: str = "prime") -> AuthorityChain:
    """Create an authority chain for Assistant operations."""
    return AuthorityChain(source="assistant", delegated_via=delegated_via)


def create_worker_authority(delegated_via: str = "assistant") -> AuthorityChain:
    """Create an authority chain for Worker operations."""
    return AuthorityChain(source="worker", delegated_via=delegated_via)


def create_controller_authority(controller_name: str, domain: str = "federated") -> AuthorityChain:
    """Create an authority chain for a federated controller."""
    return AuthorityChain(source=f"controller:{controller_name}", delegated_via=f"{domain}_control")
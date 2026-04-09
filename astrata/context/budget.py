"""Context budget tracking and management for token allocation.

This module provides the ContextBudget class for managing token budgets across
different components of the system, including system prompts, tool definitions,
conversation history, retrieval results, and task-specific content.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, validator


class ContextBudget(BaseModel):
    """Manages token budget allocation for context components.

    Tracks token usage across different categories to ensure context stays within
    model window limits and provides signals for context shaping decisions.
    """

    max_window_tokens: int = Field(..., gt=0, description="Maximum context window tokens")
    reserved_response_tokens: int = Field(
        default=0, ge=0, description="Tokens reserved for response generation"
    )
    system_prompt_tokens: int = Field(default=0, ge=0, description="Tokens used by system prompts")
    tool_definition_tokens: int = Field(
        default=0, ge=0, description="Tokens used by tool definitions"
    )
    conversation_history_tokens: int = Field(
        default=0, ge=0, description="Tokens used by conversation history"
    )
    retrieval_tokens: int = Field(default=0, ge=0, description="Tokens used by retrieval results")
    task_specific_tokens: int = Field(
        default=0, ge=0, description="Tokens used by task-specific content"
    )

    @validator("max_window_tokens")
    def validate_max_window(cls, v):
        if v <= 0:
            raise ValueError("max_window_tokens must be positive")
        return v

    @property
    def total_used_tokens(self) -> int:
        """Total tokens currently allocated across all categories."""
        return (
            self.reserved_response_tokens
            + self.system_prompt_tokens
            + self.tool_definition_tokens
            + self.conversation_history_tokens
            + self.retrieval_tokens
            + self.task_specific_tokens
        )

    @property
    def available_prompt_tokens(self) -> int:
        """Tokens available for prompt content (excluding response reservation)."""
        remaining = self.max_window_tokens - self.reserved_response_tokens
        return max(0, remaining)

    @property
    def available_tokens(self) -> int:
        """Total tokens still available in the window."""
        return max(0, self.max_window_tokens - self.total_used_tokens)

    def can_allocate(self, tokens: int) -> bool:
        """Check if the requested number of tokens can be allocated."""
        return self.available_tokens >= tokens

    def allocate_system_tokens(self, tokens: int) -> bool:
        """Allocate tokens for system prompts. Returns True if successful."""
        if not self.can_allocate(tokens):
            return False
        self.system_prompt_tokens += tokens
        return True

    def allocate_tool_tokens(self, tokens: int) -> bool:
        """Allocate tokens for tool definitions. Returns True if successful."""
        if not self.can_allocate(tokens):
            return False
        self.tool_definition_tokens += tokens
        return True

    def allocate_conversation_tokens(self, tokens: int) -> bool:
        """Allocate tokens for conversation history. Returns True if successful."""
        if not self.can_allocate(tokens):
            return False
        self.conversation_history_tokens += tokens
        return True

    def allocate_retrieval_tokens(self, tokens: int) -> bool:
        """Allocate tokens for retrieval results. Returns True if successful."""
        if not self.can_allocate(tokens):
            return False
        self.retrieval_tokens += tokens
        return True

    def allocate_task_tokens(self, tokens: int) -> bool:
        """Allocate tokens for task-specific content. Returns True if successful."""
        if not self.can_allocate(tokens):
            return False
        self.task_specific_tokens += tokens
        return True

    def deallocate_system_tokens(self, tokens: int) -> None:
        """Deallocate tokens from system prompts."""
        self.system_prompt_tokens = max(0, self.system_prompt_tokens - tokens)

    def deallocate_tool_tokens(self, tokens: int) -> None:
        """Deallocate tokens from tool definitions."""
        self.tool_definition_tokens = max(0, self.tool_definition_tokens - tokens)

    def deallocate_conversation_tokens(self, tokens: int) -> None:
        """Deallocate tokens from conversation history."""
        self.conversation_history_tokens = max(0, self.conversation_history_tokens - tokens)

    def deallocate_retrieval_tokens(self, tokens: int) -> None:
        """Deallocate tokens from retrieval results."""
        self.retrieval_tokens = max(0, self.retrieval_tokens - tokens)

    def deallocate_task_tokens(self, tokens: int) -> None:
        """Deallocate tokens from task-specific content."""
        self.task_specific_tokens = max(0, self.task_specific_tokens - tokens)

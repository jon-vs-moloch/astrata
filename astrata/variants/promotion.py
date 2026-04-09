from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

VariantLike = Mapping[str, Any]
Predicate = Callable[[VariantLike], bool]
Transform = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PromotionRule:
    """A small, explicit rule for promoting variant metadata.

    A rule matches variants via ``predicate`` and can inject or normalize fields
    via ``updates`` and ``transform``. Rules are intentionally data-oriented so
    they remain easy to declare in configuration-adjacent code.
    """

    name: str
    predicate: Predicate
    updates: Mapping[str, Any] = field(default_factory=dict)
    transform: Transform | None = None
    priority: int = 0

    def applies_to(self, variant: VariantLike) -> bool:
        return bool(self.predicate(variant))

    def apply(self, variant: VariantLike) -> dict[str, Any]:
        promoted = dict(variant)
        if self.updates:
            promoted.update(self.updates)
        if self.transform is not None:
            promoted = self.transform(promoted)
        return promoted


def promote_variant(
    variant: VariantLike,
    rules: Iterable[PromotionRule],
    *,
    stop_on_first_match: bool = False,
) -> dict[str, Any]:
    """Apply matching promotion rules to a variant.

    Rules are evaluated by descending priority so that more specific promotions
    can run before broader defaults. The input mapping is never mutated.
    """

    promoted = dict(variant)
    ordered_rules = sorted(rules, key=lambda rule: rule.priority, reverse=True)
    for rule in ordered_rules:
        if not rule.applies_to(promoted):
            continue
        promoted = rule.apply(promoted)
        if stop_on_first_match:
            break
    return promoted


def has_flag(flag: str) -> Predicate:
    """Build a predicate that checks membership in a variant's ``flags`` field."""

    def _predicate(variant: VariantLike) -> bool:
        flags = variant.get("flags", ())
        return flag in flags if isinstance(flags, (list, tuple, set, frozenset)) else False

    return _predicate


def field_equals(field_name: str, expected: Any) -> Predicate:
    """Build a predicate for exact field equality."""

    return lambda variant: variant.get(field_name) == expected


def chain_transforms(*transforms: Transform) -> Transform:
    """Compose multiple transforms into a single transform."""

    def _composed(variant: dict[str, Any]) -> dict[str, Any]:
        current = dict(variant)
        for transform in transforms:
            current = transform(current)
        return current

    return _composed

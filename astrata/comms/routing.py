"""Communication routing primitives for Astrata.

This module provides a small, dependency-free routing layer that can be
used by higher-level comms components to dispatch named messages/events to
registered handlers.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any


Handler = Callable[..., Any]


@dataclass(slots=True)
class Route:
    """Represents a named route and its registered handlers."""

    name: str
    handlers: list[Handler] = field(default_factory=list)

    def add(self, handler: Handler) -> None:
        """Attach a handler to this route if it is not already registered."""
        if handler not in self.handlers:
            self.handlers.append(handler)

    def remove(self, handler: Handler) -> None:
        """Detach a handler from this route.

        Raises:
            KeyError: If the handler is not registered.
        """
        try:
            self.handlers.remove(handler)
        except ValueError as exc:
            raise KeyError(f"handler not registered for route {self.name!r}") from exc

    def dispatch(self, *args: Any, **kwargs: Any) -> list[Any]:
        """Invoke all handlers registered on this route."""
        return [handler(*args, **kwargs) for handler in list(self.handlers)]

    def __bool__(self) -> bool:
        return bool(self.handlers)


class Router:
    """Simple in-process router for named communication channels."""

    def __init__(self) -> None:
        self._routes: dict[str, Route] = {}

    def route(self, name: str) -> Route:
        """Return the route for *name*, creating it on first access."""
        if name not in self._routes:
            self._routes[name] = Route(name=name)
        return self._routes[name]

    def register(self, name: str, handler: Handler) -> Handler:
        """Register *handler* for the named route and return it."""
        self.route(name).add(handler)
        return handler

    def unregister(self, name: str, handler: Handler) -> None:
        """Remove *handler* from the named route.

        Raises:
            KeyError: If the route or handler does not exist.
        """
        if name not in self._routes:
            raise KeyError(f"route not found: {name!r}")
        route = self._routes[name]
        route.remove(handler)
        if not route:
            del self._routes[name]

    def dispatch(self, name: str, *args: Any, **kwargs: Any) -> list[Any]:
        """Dispatch payload to all handlers registered for *name*."""
        route = self._routes.get(name)
        if route is None:
            return []
        return route.dispatch(*args, **kwargs)

    def has_route(self, name: str) -> bool:
        """Return True when a route exists and has at least one handler."""
        route = self._routes.get(name)
        return bool(route)

    def clear(self) -> None:
        """Remove all routes and handlers."""
        self._routes.clear()

    def names(self) -> tuple[str, ...]:
        """Return registered route names in sorted order."""
        return tuple(sorted(self._routes))

    def iter_routes(self) -> Iterator[Route]:
        """Iterate over current routes in sorted name order."""
        for name in self.names():
            yield self._routes[name]

    def extend(self, mapping: dict[str, Iterable[Handler]]) -> None:
        """Bulk-register handlers from a mapping of route name to handlers."""
        for name, handlers in mapping.items():
            route = self.route(name)
            for handler in handlers:
                route.add(handler)


def route_handler(router: Router, name: str) -> Callable[[Handler], Handler]:
    """Decorator that registers a function on the given router route."""

    def decorator(handler: Handler) -> Handler:
        router.register(name, handler)
        return handler

    return decorator


__all__ = ["Handler", "Route", "Router", "route_handler"]

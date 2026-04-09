"""Minimal runtime helpers for executing procedure structure."""

from __future__ import annotations

from astrata.procedures.models import ProcedureRecord, ProcedureTaskNode


class ProcedureRuntime:
    def __init__(self, procedure: ProcedureRecord) -> None:
        self.procedure = procedure
        self._node_map = procedure.structure.node_map()

    def entry_node(self) -> ProcedureTaskNode:
        return self._node_map[self.procedure.structure.entry_node_id]

    def get_node(self, node_id: str) -> ProcedureTaskNode | None:
        return self._node_map.get(node_id)

    def next_nodes(self, node_id: str) -> list[ProcedureTaskNode]:
        node = self.get_node(node_id)
        if node is None:
            return []
        resolved: list[ProcedureTaskNode] = []
        for next_id in node.next_nodes:
            next_node = self.get_node(next_id)
            if next_node is not None:
                resolved.append(next_node)
        return resolved

    def is_leaf(self, node_id: str) -> bool:
        node = self.get_node(node_id)
        if node is None:
            return False
        return node.kind == "leaf" and not node.next_nodes

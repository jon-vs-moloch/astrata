"""Local model discovery and adoption for Astrata's local-runtime substrate."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from astrata.local.models import LocalModelMetadata, LocalModelProvenance, LocalModelRecord


@dataclass(frozen=True)
class AdoptModelOptions:
    acquisition: str = "adopted"
    install_source_url: str | None = None
    catalog_id: str | None = None
    catalog_family: str | None = None
    metadata: LocalModelMetadata | None = None


@dataclass(frozen=True)
class ModelRegistryOptions:
    extra_search_roots: list[str] = field(default_factory=list)
    include_default_search_roots: bool = True
    cwd: str | None = None


DEFAULT_SEARCH_ROOTS = [
    "~/Library/Application Support/LM Studio/models",
    "~/.cache/lm-studio/models",
    "~/.lmstudio/models",
    "~/.cache/lm-studio",
    "~/.ollama/models",
    "~/models",
    "~/Downloads",
]


class LocalModelRegistry:
    def __init__(self, options: ModelRegistryOptions | None = None) -> None:
        opts = options or ModelRegistryOptions()
        self._cwd = Path(opts.cwd or Path.cwd()).resolve()
        self._extra_search_roots = list(opts.extra_search_roots)
        self._include_default_search_roots = opts.include_default_search_roots
        self._records: dict[str, LocalModelRecord] = {}

    def list_models(self) -> list[LocalModelRecord]:
        return sorted(self._records.values(), key=lambda model: model.label.lower())

    def get_model(self, model_id: str) -> LocalModelRecord | None:
        return self._records.get(model_id)

    def get_adopted_model_paths(self) -> list[str]:
        return [model.path for model in self.list_models() if model.status == "adopted"]

    def discover_models(self) -> list[LocalModelRecord]:
        roots = self._candidate_roots()
        for root in roots:
            if not root.exists():
                continue
            self._walk_for_models(root, depth=0)
        return self.list_models()

    def adopt_model(self, model_path: str, options: AdoptModelOptions | None = None) -> LocalModelRecord:
        opts = options or AdoptModelOptions()
        full_path = (self._cwd / Path(model_path).expanduser()).resolve()
        model = self._create_record(
            full_path,
            status="adopted",
            acquisition=opts.acquisition,
            install_source_url=opts.install_source_url,
            catalog_id=opts.catalog_id,
            catalog_family=opts.catalog_family,
            metadata=opts.metadata,
        )
        self._records[model.model_id] = model
        return model

    def update_model_metadata(self, model_id: str, metadata: LocalModelMetadata) -> LocalModelRecord:
        current = self._records.get(model_id)
        if current is None:
            raise KeyError(f"Unknown model: {model_id}")
        updated = current.model_copy(update={"metadata": metadata})
        self._records[model_id] = updated
        return updated

    def _candidate_roots(self) -> list[Path]:
        roots = DEFAULT_SEARCH_ROOTS if self._include_default_search_roots else []
        combined = [Path(root).expanduser() for root in [*roots, *self._extra_search_roots]]
        deduped: list[Path] = []
        seen: set[str] = set()
        for root in combined:
            key = str(root.resolve()) if root.exists() else str(root)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(root)
        return deduped

    def _walk_for_models(self, root: Path, depth: int) -> None:
        if depth > 4:
            return
        try:
            entries = list(root.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                self._walk_for_models(entry, depth + 1)
                continue
            if not entry.is_file() or entry.suffix.lower() != ".gguf":
                continue
            model = self._create_record(entry, status="discovered", acquisition="discovered")
            existing = self._records.get(model.model_id)
            if existing is None or existing.status != "adopted":
                self._records[model.model_id] = model

    def _create_record(
        self,
        model_path: Path,
        *,
        status: str,
        acquisition: str,
        install_source_url: str | None = None,
        catalog_id: str | None = None,
        catalog_family: str | None = None,
        metadata: LocalModelMetadata | None = None,
    ) -> LocalModelRecord:
        info = model_path.stat()
        label = model_path.stem
        return LocalModelRecord(
            model_id=_to_model_id(model_path),
            path=str(model_path),
            size_bytes=info.st_size,
            label=label,
            family=_infer_family(label),
            source=_infer_source(model_path),
            status=status,  # type: ignore[arg-type]
            provenance=LocalModelProvenance(
                acquisition=acquisition,  # type: ignore[arg-type]
                managed_path=_is_managed_astrata_path(model_path),
                install_source_url=install_source_url,
                catalog_id=catalog_id,
                catalog_family=catalog_family,
            ),
            metadata=metadata,
        )


def _to_model_id(model_path: Path) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in model_path.name.lower()).strip("-")


def _infer_family(label: str) -> str:
    lowered = label.lower()
    if "gemma" in lowered:
        return "gemma"
    if "llama" in lowered:
        return "llama"
    if "qwen" in lowered:
        return "qwen"
    if "mistral" in lowered:
        return "mistral"
    if "phi" in lowered:
        return "phi"
    return "unknown"


def _infer_source(model_path: Path) -> str:
    lowered = str(model_path).lower()
    if "lm studio" in lowered or "lm-studio" in lowered or ".lmstudio" in lowered:
        return "lm-studio"
    if ".ollama" in lowered:
        return "ollama"
    if ".astrata" in lowered:
        return "astrata"
    if lowered.startswith(str(Path.home()).lower()):
        return "custom"
    return "unknown"


def _is_managed_astrata_path(model_path: Path) -> bool:
    lowered = str(model_path).lower()
    managed_fragment = str(Path(".astrata") / "models").lower()
    return managed_fragment in lowered

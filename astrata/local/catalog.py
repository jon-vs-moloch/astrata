"""Curated local model starter catalog for Astrata."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogModel:
    catalog_id: str
    family: str
    label: str
    variant_label: str = ""
    parameter_scale: str = ""
    quantization: str = ""
    download_url: str | None = None
    filename: str | None = None
    recommended: bool = True
    benchmark_prior: float = 0.0
    notes: str = ""
    tags: tuple[str, ...] = ()


CATALOG_MODELS: tuple[CatalogModel, ...] = (
    CatalogModel(
        catalog_id="gemma-4",
        family="gemma",
        label="Gemma 4",
        variant_label="family",
        benchmark_prior=22.0,
        notes="Current top-tier open-weights generalist family for Astrata's local-first MVP.",
        tags=("starter", "general", "recommended"),
    ),
    CatalogModel(
        catalog_id="qwen-3.5",
        family="qwen",
        label="Qwen 3.5",
        variant_label="family",
        benchmark_prior=18.0,
        notes="Strong coding and general task family; especially useful for delegated assistant work.",
        tags=("starter", "coding", "recommended"),
    ),
    CatalogModel(
        catalog_id="qwen3-0.6b-q8_0",
        family="qwen",
        label="Qwen 3 0.6B",
        variant_label="Q8_0 GGUF",
        parameter_scale="0.6B",
        quantization="Q8_0",
        download_url="https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf?download=true",
        filename="Qwen3-0.6B-Q8_0.gguf",
        benchmark_prior=12.0,
        notes="Official tiny Qwen3 GGUF. Good candidate for a draft lane or ultra-fast local routing experiments.",
        tags=("starter", "tiny", "draft", "fast", "local-acceleration"),
    ),
    CatalogModel(
        catalog_id="qwen3.5-0.8b-q4_k_m",
        family="qwen",
        label="Qwen 3.5 0.8B",
        variant_label="Q4_K_M GGUF",
        parameter_scale="0.8B",
        quantization="Q4_K_M",
        download_url="https://huggingface.co/bartowski/Qwen_Qwen3.5-0.8B-GGUF/resolve/main/Qwen_Qwen3.5-0.8B-Q4_K_M.gguf?download=true",
        filename="Qwen_Qwen3.5-0.8B-Q4_K_M.gguf",
        benchmark_prior=14.0,
        notes="Compact Qwen 3.5 draft candidate with a much larger gap from 9B than Astrata's current local set.",
        tags=("starter", "tiny", "draft", "coding", "fast", "local-acceleration"),
    ),
    CatalogModel(
        catalog_id="lfm-2.5",
        family="lfm",
        label="LFM 2.5",
        variant_label="family",
        recommended=False,
        benchmark_prior=10.0,
        notes="Provisional: keep available while the frontier read remains open.",
        tags=("provisional",),
    ),
)


class StarterCatalog:
    def list_models(self) -> list[CatalogModel]:
        return list(CATALOG_MODELS)

    def get_model(self, catalog_id: str) -> CatalogModel | None:
        normalized = (catalog_id or "").strip().lower()
        for model in CATALOG_MODELS:
            if model.catalog_id == normalized:
                return model
        return None

    def list_installable_models(self) -> list[CatalogModel]:
        return [model for model in CATALOG_MODELS if model.download_url and model.filename]

    def family_prior(self, family: str | None) -> float:
        normalized = (family or "").strip().lower()
        for model in CATALOG_MODELS:
            if model.family == normalized:
                return model.benchmark_prior
        return 0.0

"""Runtime profiles for local inference control."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    label: str
    description: str
    llama_cpp_args: tuple[str, ...] = ()
    background_aggression: str = "normal"
    fan_policy: str = "allow"


BUILTIN_PROFILES: tuple[RuntimeProfile, ...] = (
    RuntimeProfile(
        profile_id="quiet",
        label="Quiet",
        description="Prefer low machine noise and gentle background work.",
        llama_cpp_args=(
            "-t",
            "1",
            "-ngl",
            "0",
            "--ctx-size",
            "8192",
            "--cache-ram",
            "0",
            "--no-warmup",
        ),
        background_aggression="low",
        fan_policy="avoid",
    ),
    RuntimeProfile(
        profile_id="balanced",
        label="Balanced",
        description="Sane default tradeoff between speed and quality.",
        llama_cpp_args=(),
        background_aggression="normal",
        fan_policy="moderate",
    ),
    RuntimeProfile(
        profile_id="turbo",
        label="Turbo",
        description="Prefer responsiveness and throughput over thermals.",
        llama_cpp_args=("-fa",),
        background_aggression="high",
        fan_policy="allow",
    ),
    RuntimeProfile(
        profile_id="quality",
        label="Quality",
        description="Prefer steadier output quality when thermal headroom exists.",
        llama_cpp_args=("--temp", "0.7"),
        background_aggression="normal",
        fan_policy="moderate",
    ),
)


class RuntimeProfileRegistry:
    def list_profiles(self) -> list[RuntimeProfile]:
        return list(BUILTIN_PROFILES)

    def get(self, profile_id: str) -> RuntimeProfile:
        for profile in BUILTIN_PROFILES:
            if profile.profile_id == profile_id:
                return profile
        raise KeyError(profile_id)

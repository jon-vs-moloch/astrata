"""Hardware probing for local runtime recommendations."""

from __future__ import annotations

import os
import platform
import subprocess

from astrata.local.recommendation import HardwareProfile, ThermalState


def probe_hardware_profile() -> HardwareProfile:
    system = platform.system().lower()
    machine = platform.machine().lower()
    total_memory_bytes = _probe_total_memory_bytes(system)
    return HardwareProfile(
        platform=system,
        arch=machine,
        cpu_count=os.cpu_count() or 1,
        total_memory_bytes=total_memory_bytes,
        apple_metal_likely=system == "darwin" and machine in {"arm64", "x86_64"},
    )


def _probe_total_memory_bytes(system: str) -> int:
    if system == "darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                check=True,
            )
            parsed = int(result.stdout.strip())
            if parsed > 0:
                return parsed
        except Exception:
            pass
    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0 and page_size > 0:
                return pages * page_size
        except Exception:
            pass
    return 0


def probe_thermal_state(*, preference: str = "quiet") -> ThermalState:
    normalized = preference if preference in {"quiet", "balanced", "performance"} else "quiet"
    detail = None
    telemetry_available = False
    thermal_pressure = "unknown"
    if platform.system().lower() == "darwin":
        try:
            result = subprocess.run(
                ["pmset", "-g", "therm"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if output.strip():
                telemetry_available = True
                lowered = output.lower()
                if "critical" in lowered:
                    thermal_pressure = "critical"
                elif "severe" in lowered:
                    thermal_pressure = "severe"
                elif "fair" in lowered or "warning" in lowered:
                    thermal_pressure = "fair"
                else:
                    thermal_pressure = "nominal"
                detail = output.strip().splitlines()[0]
        except Exception as exc:
            detail = str(exc)
    return ThermalState(
        preference=normalized,  # type: ignore[arg-type]
        telemetry_available=telemetry_available,
        thermal_pressure=thermal_pressure,
        fans_allowed=normalized != "quiet",
        detail=detail,
    )

"""Desktop profile configuration for Reporter.

Only source/role identity is configured here — no filesystem paths, no
secrets. Which profile a given machine uses is chosen at runtime (env var
or explicit argument), never hardcoded per Desktop, so the same code runs
unchanged on Desktop 1, 2, 3, or a future machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

PROFILE_ENV_VAR = "COMPANY_OPS_PROFILE"


@dataclass(frozen=True)
class DesktopProfile:
    name: str
    source: str
    role: str


PROFILES: Mapping[str, DesktopProfile] = {
    "DESKTOP_1": DesktopProfile(name="DESKTOP_1", source="DESKTOP_1", role="CTO_BACKEND"),
    "DESKTOP_2": DesktopProfile(name="DESKTOP_2", source="DESKTOP_2", role="CMO"),
    "DESKTOP_3": DesktopProfile(name="DESKTOP_3", source="DESKTOP_3", role="CTO_FRONTEND"),
}


class ReporterConfigError(ValueError):
    """Raised when a Reporter cannot resolve which Desktop profile to use."""


def resolve_profile(profile: str | DesktopProfile | None = None) -> DesktopProfile:
    if isinstance(profile, DesktopProfile):
        return profile

    name = profile or os.environ.get(PROFILE_ENV_VAR)
    if not name:
        raise ReporterConfigError(
            f"no Desktop profile given and {PROFILE_ENV_VAR} is not set; "
            f"choose one of {sorted(PROFILES)}"
        )
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ReporterConfigError(
            f"unknown Desktop profile: {name!r}; choose one of {sorted(PROFILES)}"
        ) from exc

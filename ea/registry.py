"""
ea/registry.py
EA Profile storage and retrieval.

Profiles are stored in ea_registry.yaml (path from config.yaml paths.ea_registry).
The registry is the single source of truth for which EAs are registered
and how to find their .set files.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from ea.schema import ParameterSchema
from ea.set_parser import SetParser


# ── EAProfile ─────────────────────────────────────────────────────────────────

@dataclass
class EAProfile:
    """Configuration for one EA registered in the optimizer."""
    name:          str           # Display name, e.g. "LEGSTECH_EA_V2"
    ex5_file:      str           # MT5 Experts file name (without .ex5 extension)
    set_template:  str           # Absolute path string to template .set file
    symbol:        str           # e.g. "XAUUSD"
    timeframe:     str           # e.g. "H1"
    mode:          str = "generic"   # "generic" | "advanced"
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Params the user has chosen to optimize (param names → True/False).
    # Empty dict means: use default_optimize logic in SetParser.
    optimize_params: dict[str, bool] = field(default_factory=dict)

    # Automation overrides: param values that must be used during backtesting,
    # regardless of what the .set template says.
    # Example: {"InpShowPanel": 0, "InpTesterMode": 1}
    automation_overrides: dict[str, object] = field(default_factory=dict)

    def __post_init__(self):
        assert self.mode in ("generic", "advanced"), \
            f"EAProfile.mode must be 'generic' or 'advanced', got {self.mode!r}"
        assert self.timeframe in (
            "M1","M5","M15","M30","H1","H4","D1","W1","MN"
        ), f"Invalid timeframe: {self.timeframe}"

    @property
    def set_template_path(self) -> Path:
        return Path(self.set_template)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EAProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── EARegistry ────────────────────────────────────────────────────────────────

class EARegistry:
    """
    Manages registered EA profiles, persisted in ea_registry.yaml.

    Usage:
        reg = EARegistry("config.yaml")
        profile = reg.get("LEGSTECH_EA_V2")
        schema  = reg.get_schema(profile)
    """

    def __init__(self, config_path: str | Path = "config.yaml"):
        config_path = Path(config_path)
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        registry_rel = cfg.get("paths", {}).get("ea_registry", "ea_registry.yaml")
        self._registry_path = config_path.parent / registry_rel
        self._parser = SetParser()
        self._profiles: dict[str, EAProfile] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def register(self, profile: EAProfile) -> None:
        """Add or update an EA profile."""
        self._profiles[profile.name] = profile
        self._save()
        logger.info(f"EARegistry: registered {profile.name!r} (mode={profile.mode})")

    def get(self, name: str) -> EAProfile:
        """Get a registered EA profile by name. Raises KeyError if not found."""
        if name not in self._profiles:
            available = list(self._profiles.keys())
            raise KeyError(
                f"EA {name!r} not registered. Available: {available}"
            )
        return self._profiles[name]

    def list_all(self) -> list[EAProfile]:
        """Return all registered profiles."""
        return list(self._profiles.values())

    def remove(self, name: str) -> None:
        """Unregister an EA."""
        self._profiles.pop(name, None)
        self._save()
        logger.info(f"EARegistry: removed {name!r}")

    def exists(self, name: str) -> bool:
        return name in self._profiles

    def get_schema(
        self,
        profile: EAProfile,
        apply_optimize_selection: bool = True,
    ) -> ParameterSchema:
        """
        Parse the EA's .set file and return a ParameterSchema.
        Applies automation_overrides to ensure headless-safe defaults.
        Applies optimize_params selection if present.
        """
        if not profile.set_template_path.exists():
            raise FileNotFoundError(
                f"Set template not found: {profile.set_template}\n"
                f"Please update the path in EA Registry for {profile.name!r}."
            )

        schema = self._parser.parse(
            path=profile.set_template_path,
            ea_name=profile.name,
            default_optimize=False,
        )

        # Apply automation overrides: force specific param values
        # (e.g. InpShowPanel=0 so no GUI renders during headless backtests)
        for pname, value in profile.automation_overrides.items():
            if pname in schema.parameters:
                schema.parameters[pname].default = value
                schema.parameters[pname].type    = "fixed"
                schema.parameters[pname].optimize = False

        if apply_optimize_selection and profile.optimize_params:
            for pname, should_opt in profile.optimize_params.items():
                if pname in schema.parameters:
                    p = schema.parameters[pname]
                    if p.type != "fixed":
                        p.optimize = should_opt
        elif not profile.optimize_params:
            # No selection yet — mark all non-fixed as optimizable by default
            for p in schema.parameters.values():
                if p.type != "fixed":
                    p.optimize = True

        return schema

    def update_optimize_params(self, name: str, optimize_params: dict[str, bool]) -> None:
        """Update which parameters to optimize for a registered EA."""
        profile = self.get(name)
        profile.optimize_params = optimize_params
        self._save()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load profiles from YAML. Creates the file if it doesn't exist."""
        if not self._registry_path.exists():
            logger.info(f"EARegistry: creating new registry at {self._registry_path}")
            self._registry_path.write_text("profiles: []\n", encoding="utf-8")
            return

        with open(self._registry_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        profiles_raw = data.get("profiles", [])
        for item in profiles_raw:
            try:
                p = EAProfile.from_dict(item)
                self._profiles[p.name] = p
            except Exception as e:
                logger.warning(f"EARegistry: skipped malformed profile {item}: {e}")

        logger.info(f"EARegistry: loaded {len(self._profiles)} profile(s) from {self._registry_path.name}")

    def _save(self) -> None:
        """Persist all profiles to YAML."""
        data = {"profiles": [p.to_dict() for p in self._profiles.values()]}
        with open(self._registry_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        logger.debug(f"EARegistry: saved {len(self._profiles)} profile(s)")

    def verify_integrity(self) -> list[str]:
        """
        Check that all registered EAs have accessible .set files.
        Returns a list of error messages (empty = all OK).
        """
        errors = []
        for name, profile in self._profiles.items():
            if not profile.set_template_path.exists():
                errors.append(f"{name}: .set file missing at {profile.set_template}")
        return errors

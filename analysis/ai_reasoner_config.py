"""
analysis/ai_reasoner_config.py
Loads the Anthropic API key from config.yaml or environment.
Add this to config.yaml:

  ai:
    anthropic_api_key: "sk-ant-..."
    enabled: true
"""
from __future__ import annotations
import os
from pathlib import Path
import yaml


def load_api_key(config_path: str | Path = "config.yaml") -> str:
    """
    Load the Anthropic API key. Priority:
    1. ANTHROPIC_API_KEY environment variable
    2. config.yaml ai.anthropic_api_key
    3. Empty string (fallback mode)
    """
    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_key:
        return env_key

    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        return cfg.get("ai", {}).get("anthropic_api_key", "")
    except Exception:
        return ""


def is_ai_enabled(config_path: str | Path = "config.yaml") -> bool:
    """Check if AI reasoning is enabled in config."""
    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_key:
        return True
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        ai_cfg = cfg.get("ai", {})
        return ai_cfg.get("enabled", False) and bool(ai_cfg.get("anthropic_api_key", ""))
    except Exception:
        return False

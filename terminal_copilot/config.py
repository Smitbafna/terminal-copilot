"""YAML configuration loader for Terminal Copilot."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from terminal_copilot.models import AppConfig


DEFAULT_CONFIG_PATH = Path.home() / ".terminal-copilot" / "config.yaml"


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load configuration from a YAML file.

    Args:
        path: Path to the config file. Defaults to ~/.terminal-copilot/config.yaml.

    Returns:
        An AppConfig instance with the parsed configuration.

    Raises:
        FileNotFoundError: If the config file does not exist.
        yaml.YAMLError: If the config file contains invalid YAML.
    """
    config_path = path or DEFAULT_CONFIG_PATH

    if not config_path.exists():
        return AppConfig(plugins=[])

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return AppConfig(plugins=[])

    return AppConfig(**raw)
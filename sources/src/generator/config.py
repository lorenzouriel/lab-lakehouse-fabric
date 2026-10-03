"""Configuration loading and project paths."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parent.parent
ROOT = SRC_DIR.parent
DATA_DIR = ROOT / "data"
DEFAULT_CONFIG = SRC_DIR / "config" / "fruit_juice.toml"
CALIBRATION_DIR = SRC_DIR / "calibration"
REFERENCE_DIR = SRC_DIR / "reference"   # the six static dimensions of the original dataset


def load_config(path: Path | str | None = None, **overrides: Any) -> dict[str, Any]:
    """Load the TOML config; overrides use dotted keys, e.g. ``**{"period.end": "2015-12-31"}``."""
    with open(path or DEFAULT_CONFIG, "rb") as fh:
        cfg = tomllib.load(fh)
    for dotted, value in overrides.items():
        node = cfg
        *parents, leaf = dotted.split(".")
        for part in parents:
            node = node[part]
        node[leaf] = value
    return cfg


def calibration_path(cfg: dict[str, Any]) -> Path:
    return CALIBRATION_DIR / f"{cfg['dataset']}.json"

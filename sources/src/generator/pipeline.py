"""generate: calibration + config + seed -> dataset files."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR, REFERENCE_DIR, calibration_path
from .dims import load_static_dimensions
from .facts import generate_year
from .model import Model, load_calibration
from .timedim import build_dim_tempo
from .writers import DatasetWriter


def generate(cfg: dict[str, Any], data_dir: Path = DATA_DIR,
             reference_dir: Path = REFERENCE_DIR, log: Callable[[str], None] = print) -> dict[str, int]:
    start = date.fromisoformat(cfg["period"]["start"])
    end = date.fromisoformat(cfg["period"]["end"])
    calibration = load_calibration(calibration_path(cfg))
    if start < date(calibration["anchor_years"][0], 1, 1):
        raise ValueError(f"period.start must be on or after {calibration['anchor_years'][0]}-01-01")
    if end < start:
        raise ValueError("period.end is before period.start")

    model = Model(calibration, cfg, end.year)
    writer = DatasetWriter(data_dir, cfg["dataset"], cfg["output"]["formats"])
    log("  formats: " + ", ".join(writer.formats))
    counts: dict[str, int] = {}
    try:
        for name, table in load_static_dimensions(reference_dir).items():
            writer.write_dimension(name, table)
            counts[name] = table.num_rows
        tempo = build_dim_tempo(start, end)
        writer.write_dimension("dim_tempo", tempo)
        counts["dim_tempo"] = tempo.num_rows

        for year in range(start.year, end.year + 1):
            tables = generate_year(model, year, start, end)
            writer.write_facts_year(year, tables)
            for name, table in tables.items():
                counts[name] = counts.get(name, 0) + table.num_rows
            log(f"  {year}: " + ", ".join(f"{n[-3:]}={t.num_rows:,}" for n, t in tables.items()))
    finally:
        writer.close()
    return counts

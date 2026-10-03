"""Validate a generated dataset: integrity, key invariants and value sanity.

    uv run python -m generator validate
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.dataset as pads
import pyarrow.json as pajson

from .facts import FACT_SCHEMAS
from .schema import (DERIVED_FROM_FATO001 as DERIVED, DIMENSION_SCHEMAS, FACT_FOREIGN_KEYS as FOREIGN_KEYS,
                     MEASURES, PRIMARY_KEYS)

@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _load_table(root: Path, fmt: str, name: str, schema: pa.Schema) -> pa.Table:
    if fmt == "parquet":
        table = pads.dataset(root / name, format="parquet", partitioning="hive").to_table()
        return table.drop_columns(["year"]) if "year" in table.column_names else table
    if fmt == "json":
        # the JSON reader cannot parse date strings directly: read them as text, then cast
        read_schema = pa.schema([pa.field(f.name, pa.string() if pa.types.is_date(f.type) else f.type) for f in schema])
        table = pajson.read_json(root / f"{name}.jsonl", parse_options=pajson.ParseOptions(explicit_schema=read_schema))
        return table.cast(schema)
    return pacsv.read_csv(root / f"{name}.csv",
                          convert_options=pacsv.ConvertOptions(column_types=schema, strings_can_be_null=True,
                                                               null_values=[""]))


def load_dataset(cfg: dict[str, Any], data_dir: Path, fmt: str) -> dict[str, pa.Table]:
    root = data_dir / fmt / cfg["dataset"]
    if not root.exists():
        raise FileNotFoundError(f"{root} not found - run `generate` first (format {fmt!r})")
    tables = {n: _load_table(root, fmt, n, s) for n, s in FACT_SCHEMAS.items()}
    tables.update({n: _load_table(root, fmt, n, s) for n, s in DIMENSION_SCHEMAS.items()})
    return tables


def _distinct(table: pa.Table, cols: list[str]) -> pa.Table:
    return table.select(cols).group_by(cols).aggregate([])


def _anti(left: pa.Table, right: pa.Table, cols: list[str]) -> int:
    return left.join(right, keys=cols, join_type="left anti").num_rows


def _monthly_sum(table: pa.Table, col: str) -> dict[str, float]:
    month = pc.utf8_slice_codeunits(table["COD_DIA"], 0, 6)
    agg = pa.table({"m": month, "v": table[col]}).group_by("m").aggregate([("v", "sum")])
    return dict(zip(agg["m"].to_pylist(), agg["v_sum"].to_pylist()))


def _sum_by_year(monthly: dict[str, float]) -> dict[int, float]:
    out: dict[int, float] = {}
    for ym, v in monthly.items():
        out[int(ym[:4])] = out.get(int(ym[:4]), 0.0) + v
    return out


def validate(cfg: dict[str, Any], data_dir: Path, fmt: str = "parquet") -> list[Check]:
    checks: list[Check] = []
    add = lambda name, ok, detail="": checks.append(Check(name, bool(ok), detail))  # noqa: E731
    data = load_dataset(cfg, data_dir, fmt)
    f1 = data["tab_fato001"]
    start, end = date.fromisoformat(cfg["period"]["start"]), date.fromisoformat(cfg["period"]["end"])

    # --- structure ------------------------------------------------------------------------
    for name in FACT_SCHEMAS:
        add(f"{name}: not empty", data[name].num_rows > 0, f"{data[name].num_rows:,} rows")
        nulls = sum(data[name][c].null_count for c in data[name].column_names)
        add(f"{name}: no nulls", nulls == 0, f"{nulls} nulls")
        add(f"{name}: primary key unique", _distinct(data[name], PRIMARY_KEYS[name]).num_rows == data[name].num_rows)
        for col, (dim, dim_col) in FOREIGN_KEYS.items():
            if col in data[name].column_names:
                ok = pc.all(pc.is_in(data[name][col], value_set=data[dim][dim_col])).as_py()
                add(f"{name}.{col} -> {dim}", ok)
        for m in MEASURES[name]:
            add(f"{name}.{m} >= 0", pc.min(data[name][m]).as_py() >= 0, f"min={pc.min(data[name][m]).as_py()}")

    # --- key invariants relative to Fato_001 -------------------------------------------------
    for name, cols in DERIVED.items():
        base = _distinct(f1, cols)
        add(f"{name}: keys == distinct Fato_001 {cols}",
            _anti(data[name], base, cols) == 0 and _anti(base, data[name], cols) == 0)
    add("tab_fato002: one row per Fato_001 row", data["tab_fato002"].num_rows == f1.num_rows)

    # --- coverage --------------------------------------------------------------------------
    tempo = data["dim_tempo"]
    add("dim_tempo: one row per day", tempo.num_rows == (end - start).days + 1, f"{tempo.num_rows:,} days")
    lo, hi = pc.min_max(f1["DATA_FATO"]).values()
    add("Fato_001: dates inside period", lo.as_py().date() >= start and hi.as_py().date() <= end,
        f"{lo.as_py().date()} .. {hi.as_py().date()}")
    years_present = set(pc.unique(pc.year(f1["DATA_FATO"])).to_pylist())
    add("Fato_001: every year present", years_present == set(range(start.year, end.year + 1)))

    # --- value sanity -------------------------------------------------------------------------
    fat, tax, cost = (f1[c].to_numpy() for c in ("FATURAMENTO", "IMPOSTO", "CUSTO_VARIAVEL"))
    add("Fato_001: FATURAMENTO > 0", fat.min() > 0)
    add("Fato_001: tax and variable cost below revenue", ((tax + cost) < fat).mean() > 0.999,
        f"{((tax + cost) < fat).mean():.4%} of rows")
    add("Fato_001: tax rate <= 25%", (tax / fat).max() <= 0.25, f"max={(tax / fat).max():.3f}")

    # --- year-over-year sanity ------------------------------------------------------------------
    yearly = _sum_by_year(_monthly_sum(f1, "FATURAMENTO"))
    ys = sorted(yearly)
    full = [y for y in ys if date(y, 1, 1) >= start and date(y, 12, 31) <= end]
    growth = {y: yearly[y] / yearly[y - 1] - 1 for y in full if y - 1 in full}
    bad = {y: g for y, g in growth.items() if not -0.10 <= g <= 0.60}
    add("revenue: yearly growth within [-10%, +60%]", not bad,
        ", ".join(f"{y}:{g:+.0%}" for y, g in growth.items()))

    return checks


def report(checks: list[Check]) -> bool:
    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name}" + (f"  ({c.detail})" if c.detail else ""))
    failed = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return not failed

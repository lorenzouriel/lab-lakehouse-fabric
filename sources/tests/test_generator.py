import hashlib

import pyarrow.parquet as pq

from conftest import make_cfg
from generator.pipeline import generate
from generator.validate import validate


def _digest(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root / "csv" / "fruit_juice").glob("*.csv"))}


def _all_files_digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_same_seed_is_byte_identical(base_cfg, tmp_path):
    cfg_a, out_a = make_cfg(tmp_path / "a", "2014-06-30")
    cfg_b, out_b = make_cfg(tmp_path / "b", "2014-06-30")
    generate(cfg_a, out_a, log=lambda _: None)
    generate(cfg_b, out_b, log=lambda _: None)
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")


def test_different_seed_changes_facts_not_dimensions(base_cfg, tmp_path):
    cfg_a, out_a = make_cfg(tmp_path / "a", "2013-12-31", seed=1)
    cfg_b, out_b = make_cfg(tmp_path / "b", "2013-12-31", seed=2)
    generate(cfg_a, out_a, log=lambda _: None)
    generate(cfg_b, out_b, log=lambda _: None)
    a, b = _digest(tmp_path / "a"), _digest(tmp_path / "b")
    assert a["tab_fato001.csv"] != b["tab_fato001.csv"]
    assert a["dim_cliente.csv"] == b["dim_cliente.csv"]


def test_extending_the_period_does_not_change_earlier_years(base_cfg, tmp_path):
    cfg_short, out_short = make_cfg(tmp_path / "short", "2017-12-31")
    cfg_long, out_long = make_cfg(tmp_path / "long", "2019-12-31")
    generate(cfg_short, out_short, log=lambda _: None)
    generate(cfg_long, out_long, log=lambda _: None)
    for table in ("tab_fato001", "tab_fato002", "tab_fato003", "tab_fato004", "tab_fato005"):
        for year in range(2013, 2018):
            short = pq.read_table(tmp_path / "short" / "parquet" / "fruit_juice" / table / f"year={year}")
            long = pq.read_table(tmp_path / "long" / "parquet" / "fruit_juice" / table / f"year={year}")
            assert short.equals(long), (table, year)


def test_generated_dataset_passes_validation(base_cfg, tmp_path):
    cfg, out = make_cfg(tmp_path, "2016-12-31", formats=["csv", "parquet", "json"])
    generate(cfg, out, log=lambda _: None)
    failed = [c for c in validate(cfg, out, "parquet") if not c.ok]
    assert not failed, failed
    for fmt in ("csv", "json"):
        failed_other = [c for c in validate(cfg, out, fmt) if not c.ok]
        assert not failed_other, (fmt, failed_other)


def test_partial_period_is_respected(base_cfg, tmp_path):
    cfg, out = make_cfg(tmp_path, "2014-03-15", **{"period.start": "2013-11-10"})
    generate(cfg, out, log=lambda _: None)
    table = pq.read_table(tmp_path / "parquet" / "fruit_juice" / "tab_fato001")
    days = table["COD_DIA"].to_pylist()
    assert min(days) >= "20131110" and max(days) <= "20140315"
    assert pq.read_table(tmp_path / "parquet" / "fruit_juice" / "dim_tempo").num_rows == 126


# ---- extra formats ---------------------------------------------------------------------------------

import json  # noqa: E402

import pyarrow.compute as pc  # noqa: E402
import pyarrow.dataset as pads  # noqa: E402
import sqlglot  # noqa: E402

from generator.schema import FOREIGN_KEYS, LOAD_ORDER, PRIMARY_KEYS  # noqa: E402
from generator.writers import resolve_formats  # noqa: E402


def _short_run(tmp_path, formats):
    cfg, out = make_cfg(tmp_path, "2013-03-31", formats=formats)
    counts = generate(cfg, out, log=lambda _: None)
    return cfg, out, counts


def test_sql_formats_pull_in_csv():
    assert resolve_formats(["sqlserver"]) == ["csv", "sqlserver"]
    assert resolve_formats(["postgresql", "mongodb"]) == ["csv", "mongodb", "postgresql"]


def test_unknown_format_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        resolve_formats(["excel"])


def test_json_lines_match_parquet(tmp_path):
    cfg, out, counts = _short_run(tmp_path, ["parquet", "json"])
    parquet = pads.dataset(out / "parquet" / "fruit_juice" / "tab_fato001", format="parquet", partitioning="hive").to_table()
    lines = (out / "json" / "fruit_juice" / "tab_fato001.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == parquet.num_rows == counts["tab_fato001"]
    first = json.loads(lines[0])
    assert first["DATA_FATO"] == "2013-01-01T00:00:00.000"
    assert isinstance(first["COD_FABRICA"], str) and isinstance(first["FATURAMENTO"], float)
    total = sum(json.loads(line)["FATURAMENTO"] for line in lines)
    assert abs(total - pc.sum(parquet["FATURAMENTO"]).as_py()) < 1e-6 * total
    dim = [json.loads(line) for line in (out / "json" / "fruit_juice" / "dim_organizacional.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["Cod_Pai"] is None for row in dim)          # NULL survives as JSON null
    assert all(isinstance(row["Esquerda"], int) for row in dim)


def test_mongodb_files(tmp_path):
    cfg, out, counts = _short_run(tmp_path, ["mongodb"])
    root = out / "mongodb" / "fruit_juice"
    sales = [json.loads(line) for line in (root / "sales.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(sales) == counts["tab_fato001"]
    doc = sales[0]
    assert doc["_id"] == f'{doc["cod_dia"]}|{doc["cliente"]["cod"]}|{doc["produto"]["cod"]}|{doc["fabrica"]["cod"]}'
    assert doc["data"] == {"$date": "2013-01-01T00:00:00.000Z"}
    assert set(doc["cliente"]) == {"cod", "desc", "cidade", "uf", "estado", "regiao", "segmento"}
    assert set(doc["produto"]) == {"cod", "desc", "tamanho", "sabor", "marca", "categoria"}
    assert isinstance(doc["frete"], float) and isinstance(doc["quantidade"], float)
    assert len({d["_id"] for d in sales}) == len(sales)
    flat = [json.loads(line) for line in (root / "tab_fato001.jsonl").read_text(encoding="utf-8").splitlines()]
    assert abs(sum(d["faturamento"] for d in sales) - sum(d["FATURAMENTO"] for d in flat)) < 1e-6 * sum(d["FATURAMENTO"] for d in flat)
    freight = sum(json.loads(line)["FRETE"] for line in (root / "tab_fato002.jsonl").read_text(encoding="utf-8").splitlines())
    assert abs(sum(d["frete"] for d in sales) - freight) < 1e-6 * freight
    assert "sales" in (root / "import.sh").read_text(encoding="utf-8")
    assert "createIndex" in (root / "indexes.js").read_text(encoding="utf-8")


def test_sql_scripts_parse_and_are_complete(tmp_path):
    cfg, out, _ = _short_run(tmp_path, ["postgresql", "sqlserver"])
    for engine, dialect in (("postgresql", "postgres"), ("sqlserver", "tsql")):
        root = out / engine / "fruit_juice"
        schema_sql = (root / "01_schema.sql").read_text(encoding="utf-8")
        for name in ("01_schema.sql", "03_constraints.sql", "04_rowcounts.sql"):
            text = (root / name).read_text(encoding="utf-8")
            statements = [s for s in sqlglot.parse(text.replace("\nGO\n", ";\n"), read=dialect) if s is not None]
            assert statements, (engine, name)
        creates = schema_sql.count("CREATE TABLE")
        assert creates == len(LOAD_ORDER)
        constraints = (root / "03_constraints.sql").read_text(encoding="utf-8")
        assert constraints.count("PRIMARY KEY") == len(PRIMARY_KEYS)
        assert constraints.count("FOREIGN KEY") == len(FOREIGN_KEYS)
        load = (root / "02_load.sql").read_text(encoding="utf-8")
        for table in LOAD_ORDER:
            assert f"{table}.csv" in load
        assert (root / "load.sh").exists() and (root / "README.md").exists()
    assert r"\copy" in (out / "postgresql" / "fruit_juice" / "02_load.sql").read_text(encoding="utf-8")
    assert "BULK INSERT" in (out / "sqlserver" / "fruit_juice" / "02_load.sql").read_text(encoding="utf-8")
    assert (out / "csv" / "fruit_juice" / "tab_fato001.csv").exists()

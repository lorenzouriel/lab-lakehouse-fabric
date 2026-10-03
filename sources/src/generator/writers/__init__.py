"""Output formats. Each writes ``data/<format>/<dataset>/``; ``DatasetWriter`` fans out to all selected."""
from __future__ import annotations

import shutil
from pathlib import Path

import pyarrow as pa

from .base import FormatWriter
from .jsonl import JsonWriter
from .mongodb import MongoWriter
from .sqlwriter import PostgresWriter, SqlServerWriter
from .tabular import CsvWriter, ParquetWriter

WRITERS: dict[str, type[FormatWriter]] = {
    "csv": CsvWriter, "parquet": ParquetWriter, "json": JsonWriter, "mongodb": MongoWriter,
    "postgresql": PostgresWriter, "sqlserver": SqlServerWriter,
}
FORMATS = list(WRITERS)


def resolve_formats(requested: list[str]) -> list[str]:
    """Validate the names and add formats the others depend on (the SQL scripts read the CSVs)."""
    unknown = sorted(set(requested) - set(WRITERS))
    if unknown:
        raise ValueError(f"unsupported output formats {unknown}; supported: {FORMATS}")
    wanted = set(requested)
    for name in requested:
        wanted.update(WRITERS[name].requires)
    return [f for f in FORMATS if f in wanted]


class DatasetWriter:
    def __init__(self, data_dir: Path, dataset: str, formats: list[str], clean: bool = True):
        self.formats = resolve_formats(formats)
        self.writers: list[FormatWriter] = []
        for fmt in self.formats:
            root = data_dir / fmt / dataset
            if clean and root.exists():
                shutil.rmtree(root)   # only this dataset's folder for this format
            root.mkdir(parents=True, exist_ok=True)
            self.writers.append(WRITERS[fmt](root, dataset))

    def write_dimension(self, name: str, table: pa.Table) -> None:
        for writer in self.writers:
            writer.write_dimension(name, table)

    def write_facts_year(self, year: int, tables: dict[str, pa.Table]) -> None:
        for writer in self.writers:
            writer.write_facts_year(year, tables)

    def close(self) -> None:
        for writer in self.writers:
            writer.close()

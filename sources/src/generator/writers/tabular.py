"""CSV and Parquet.

    csv/<dataset>/<table>.csv                            one file per table (UTF-8, header row)
    parquet/<dataset>/<table>/part-0.parquet             dimensions
    parquet/<dataset>/<table>/year=YYYY/part-0.parquet   facts, hive-partitioned by year
"""
from __future__ import annotations

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from .base import FormatWriter


def _csv_options() -> pacsv.WriteOptions:
    return pacsv.WriteOptions(include_header=True, quoting_style="needed")


class CsvWriter(FormatWriter):
    name = "csv"

    def __init__(self, root, dataset):
        super().__init__(root, dataset)
        self._open: dict[str, pacsv.CSVWriter] = {}

    def write_dimension(self, name: str, table: pa.Table) -> None:
        pacsv.write_csv(table, self.root / f"{name}.csv", _csv_options())

    def write_facts_year(self, year: int, tables: dict[str, pa.Table]) -> None:
        for name, table in tables.items():
            if name not in self._open:
                self._open[name] = pacsv.CSVWriter(self.root / f"{name}.csv", table.schema,
                                                   write_options=_csv_options())
            self._open[name].write_table(table)

    def close(self) -> None:
        for writer in self._open.values():
            writer.close()
        self._open.clear()


class ParquetWriter(FormatWriter):
    name = "parquet"

    def write_dimension(self, name: str, table: pa.Table) -> None:
        target = self.root / name
        target.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, target / "part-0.parquet", compression="zstd")

    def write_facts_year(self, year: int, tables: dict[str, pa.Table]) -> None:
        for name, table in tables.items():
            target = self.root / name / f"year={year}"
            target.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, target / "part-0.parquet", compression="zstd")

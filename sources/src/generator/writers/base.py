"""Common interface of the per-format writers."""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa


class FormatWriter:
    """Writes one dataset in one format under ``data/<format>/<dataset>/``."""

    name = ""
    requires: tuple[str, ...] = ()   # other formats whose files this format's scripts point at

    def __init__(self, root: Path, dataset: str):
        self.root = root
        self.dataset = dataset

    def write_dimension(self, name: str, table: pa.Table) -> None:
        raise NotImplementedError

    def write_facts_year(self, year: int, tables: dict[str, pa.Table]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

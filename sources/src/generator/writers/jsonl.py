"""JSON Lines (NDJSON): one JSON object per line, one file per table.

    json/<dataset>/<table>.jsonl

Built with vectorised Arrow string kernels (a per-row ``json.dumps`` loop would take minutes for
the ~2.4M fact rows). Plain JSON here: timestamps and dates are ISO-8601 strings and numbers are
JSON numbers; ``mongo=True`` produces MongoDB Extended JSON instead (dates as ``{"$date": ...}``).
"""
from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc

from .base import FormatWriter

_ESCAPES = (("\\", "\\\\"), ('"', '\\"'), ("\n", "\n"), ("\r", "\r"), ("\t", "\t"))


def lit(text: str) -> pa.Scalar:
    return pa.scalar(text, type=pa.string())


def join(*parts) -> pa.Array:
    """Element-wise concatenation of string arrays and scalars."""
    return pc.binary_join_element_wise(*parts, lit(""))


def json_string(col) -> pa.Array:
    """Quoted, escaped JSON string; NULL stays NULL (callers turn it into ``null``)."""
    for old, new in _ESCAPES:
        col = pc.replace_substring(col, old, new)
    return join(lit('"'), col, lit('"'))


def json_number(col, force_float: bool) -> pa.Array:
    text = pc.cast(col, pa.string())
    if force_float:   # keep whole floats as 50.0 so MongoDB stores doubles, not a mix of int/double
        has_point = pc.or_(pc.match_substring(text, "."), pc.match_substring(text, "e"))
        text = pc.if_else(has_point, text, join(text, lit(".0")))
    return text


def json_value(col, field: pa.Field, mongo: bool) -> pa.Array:
    col = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
    typ = field.type
    if pa.types.is_string(typ):
        out = json_string(col)
    elif pa.types.is_floating(typ):
        out = json_number(col, force_float=True)
    elif pa.types.is_integer(typ):
        out = json_number(col, force_float=False)
    elif pa.types.is_timestamp(typ) or pa.types.is_date(typ):
        if pa.types.is_timestamp(typ):
            # Whole seconds only (safe cast raises if there were ever a sub-second part): Arrow's %S would
            # otherwise print the fraction itself, and its width depends on the timestamp unit.
            text = join(pc.strftime(col.cast(pa.timestamp("s")), format="%Y-%m-%dT%H:%M:%S"), lit(".000"))
        else:
            text = pc.strftime(col, format="%Y-%m-%d")
        if mongo:
            if pa.types.is_date(typ):
                text = join(text, lit("T00:00:00.000"))
            out = join(lit('{"$date":"'), text, lit('Z"}'))
        else:
            out = join(lit('"'), text, lit('"'))
    else:
        raise TypeError(f"no JSON mapping for {typ}")
    return pc.fill_null(out, lit("null"))


def to_jsonl(table: pa.Table, mongo: bool = False) -> str:
    """Serialise a table to JSON Lines text (with a trailing newline)."""
    if table.num_rows == 0:
        return ""
    members = [join(lit(f'"{field.name}":'), json_value(table[field.name], field, mongo))
               for field in table.schema]
    rows = join(lit("{"), pc.binary_join_element_wise(*members, lit(",")), lit("}"))
    return "\n".join(rows.to_pylist()) + "\n"


class JsonWriter(FormatWriter):
    name = "json"
    mongo = False
    suffix = ".jsonl"

    def __init__(self, root, dataset):
        super().__init__(root, dataset)
        self._started: set[str] = set()

    def _append(self, name: str, table: pa.Table) -> None:
        mode = "a" if name in self._started else "w"
        self._started.add(name)
        with open(self.root / f"{name}{self.suffix}", mode, encoding="utf-8", newline="\n") as fh:
            fh.write(to_jsonl(table, mongo=self.mongo))

    def write_dimension(self, name: str, table: pa.Table) -> None:
        self._append(name, table)

    def write_facts_year(self, year: int, tables: dict[str, pa.Table]) -> None:
        for name, table in tables.items():
            self._append(name, table)

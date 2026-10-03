"""SQL Server and PostgreSQL: DDL + bulk-load scripts over the shared CSV files.

    <engine>/<dataset>/01_schema.sql       schema + tables (no constraints, so the load is fast)
    <engine>/<dataset>/02_load.sql         bulk load of ../../csv/<dataset>/*.csv
    <engine>/<dataset>/03_constraints.sql  primary keys, then foreign keys (also validates the data)
    <engine>/<dataset>/04_rowcounts.sql    row count per table
    <engine>/<dataset>/load.sh             connects using the repo-root .env, creates the database if needed, runs 01-04
    <engine>/<dataset>/README.md

The data itself is not duplicated: the load scripts read the CSV files under ``data/csv/<dataset>/``.
The pure script generators are here; the writer class and shell/README templates are in sqlwriter.py.
"""
from __future__ import annotations

import pyarrow as pa

from ..schema import FOREIGN_KEYS, LOAD_ORDER, PRIMARY_KEYS, TABLE_SCHEMAS
from .base import FormatWriter


def _length(column: str) -> int:
    return 200 if column.startswith(("Desc_", "Atr_")) else 50


class Dialect:
    name = ""
    script_sep = ""          # statement batch separator, e.g. "GO" for sqlcmd

    def ident(self, name: str) -> str:
        raise NotImplementedError

    def table(self, schema: str, name: str) -> str:
        return f"{self.ident(schema)}.{self.ident(name)}"

    def sql_type(self, field: pa.Field) -> str:
        raise NotImplementedError

    def drop_table(self, schema: str, name: str) -> str:
        raise NotImplementedError

    def create_schema(self, schema: str) -> str:
        raise NotImplementedError

    def load_statement(self, schema: str, table: str, columns: list[str], dataset: str) -> str:
        raise NotImplementedError

    def batch(self, statements: list[str]) -> str:
        body = "\n".join(statements)
        return f"{body}\n{self.script_sep}\n" if self.script_sep else body + "\n"


class Postgres(Dialect):
    name = "postgresql"

    def ident(self, name):
        return name.lower()

    def sql_type(self, field):
        t = field.type
        if pa.types.is_string(t):
            return f"VARCHAR({_length(field.name)})"
        if pa.types.is_floating(t):
            return "DOUBLE PRECISION"
        if pa.types.is_integer(t):
            return "INTEGER"
        if pa.types.is_date(t):
            return "DATE"
        if pa.types.is_timestamp(t):
            return "TIMESTAMP(3)"
        raise TypeError(t)

    def drop_table(self, schema, name):
        return f"DROP TABLE IF EXISTS {self.table(schema, name)} CASCADE;"

    def create_schema(self, schema):
        return f"CREATE SCHEMA IF NOT EXISTS {self.ident(schema)};"

    def load_statement(self, schema, table, columns, dataset):
        cols = ", ".join(self.ident(c) for c in columns)
        # \copy must stay on one line; the path is relative to the folder holding these scripts
        return (f"\\copy {self.table(schema, table)} ({cols}) FROM '../../csv/{dataset}/{table}.csv' "
                f"WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')")


class SqlServer(Dialect):
    name = "sqlserver"
    script_sep = "GO"

    def ident(self, name):
        return f"[{name}]"

    def sql_type(self, field):
        t = field.type
        if pa.types.is_string(t):
            return f"NVARCHAR({_length(field.name)})"
        if pa.types.is_floating(t):
            return "FLOAT"
        if pa.types.is_integer(t):
            return "INT"
        if pa.types.is_date(t):
            return "DATE"
        if pa.types.is_timestamp(t):
            return "DATETIME2(3)"
        raise TypeError(t)

    def drop_table(self, schema, name):
        return f"DROP TABLE IF EXISTS {self.table(schema, name)};"

    def create_schema(self, schema):
        return f"IF SCHEMA_ID(N'{schema}') IS NULL EXEC(N'CREATE SCHEMA {self.ident(schema)}');"

    def load_statement(self, schema, table, columns, dataset):
        # The CSVs use LF line endings, hence ROWTERMINATOR (SQL Server's CSV mode defaults to CRLF).
        # $(CodePageOption) is "CODEPAGE = '65001'," on Windows (files are UTF-8) and "MAXERRORS = 0," on Linux, where the
        # option is not supported and UTF-8 is what BULK INSERT expects anyway; load.sh sets it.
        # $(DataDir) is a sqlcmd scripting variable (taken from the environment): the CSV folder as SQL Server sees it
        return (f"BULK INSERT {self.table(schema, table)} FROM '$(DataDir)/{table}.csv' WITH "
                "(FORMAT = 'CSV', FIELDQUOTE = '\"', FIRSTROW = 2, ROWTERMINATOR = '0x0a', $(CodePageOption) KEEPNULLS, TABLOCK);")


def schema_script(d: Dialect, schema: str) -> str:
    out = [f"-- {schema}: tables (constraints are added by 03_constraints.sql after the load)", d.create_schema(schema)]
    if d.script_sep:
        out.append(d.script_sep)
    out += [d.drop_table(schema, t) for t in reversed(LOAD_ORDER)]
    if d.script_sep:
        out.append(d.script_sep)
    for table in LOAD_ORDER:
        pk = set(PRIMARY_KEYS[table])
        cols = [f"    {d.ident(f.name)} {d.sql_type(f)}{' NOT NULL' if f.name in pk else ' NULL'}"
                for f in TABLE_SCHEMAS[table]]
        out.append(f"CREATE TABLE {d.table(schema, table)} (\n" + ",\n".join(cols) + "\n);")
    return d.batch(out)


def load_script(d: Dialect, schema: str, dataset: str) -> str:
    stmts = [f"-- {schema}: bulk load from the CSV files (dimensions first)"]
    if d.name == "sqlserver":
        stmts.append("SET NOCOUNT ON;")
    for table in LOAD_ORDER:
        stmts.append(d.load_statement(schema, table, TABLE_SCHEMAS[table].names, dataset))
    return d.batch(stmts)


def constraints_script(d: Dialect, schema: str) -> str:
    stmts = [f"-- {schema}: primary keys, then foreign keys (fails if the data is inconsistent)"]
    for table in LOAD_ORDER:
        cols = ", ".join(d.ident(c) for c in PRIMARY_KEYS[table])
        stmts.append(f"ALTER TABLE {d.table(schema, table)} ADD CONSTRAINT {d.ident('pk_' + table)} "
                     f"PRIMARY KEY {'CLUSTERED ' if d.name == 'sqlserver' else ''}({cols});")
    for table, col, ref, ref_col in FOREIGN_KEYS:
        stmts.append(f"ALTER TABLE {d.table(schema, table)} ADD CONSTRAINT {d.ident(f'fk_{table}_{col.lower()}')} "
                     f"FOREIGN KEY ({d.ident(col)}) REFERENCES {d.table(schema, ref)} ({d.ident(ref_col)});")
    if d.name == "postgresql":
        stmts.append("ANALYZE;")
    return d.batch(stmts)


def rowcounts_script(d: Dialect, schema: str) -> str:
    selects = [f"SELECT '{t}' AS table_name, COUNT(*) AS row_count FROM {d.table(schema, t)}" for t in LOAD_ORDER]
    return d.batch([" UNION ALL\n".join(selects) + ";"])



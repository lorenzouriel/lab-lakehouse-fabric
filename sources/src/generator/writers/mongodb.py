"""MongoDB: Extended-JSON files for ``mongoimport`` plus an index script.

    mongodb/<dataset>/<table>.jsonl   flat collections, same columns as the tables (dates as {"$date": ...})
    mongodb/<dataset>/sales.jsonl     denormalised: each tab_fato001 row with client, product, factory,
                                      organisation and the row's freight (tab_fato002) embedded
    mongodb/<dataset>/import.sh       mongoimport --drop for every file, then indexes.js
    mongodb/<dataset>/indexes.js      unique indexes on the natural keys + query indexes on sales
"""
from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.compute as pc

from ..schema import LOAD_ORDER, PRIMARY_KEYS
from .envfile import ENV_LOADER
from .jsonl import JsonWriter, join, json_string, json_value, lit


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


IMPORT_SH = r"""#!/usr/bin/env bash
# Load every collection with mongoimport (--drop makes it re-runnable), then create the indexes.
# Connection: MONGO_* from the repo-root .env (or the environment).
#   MONGO_URI                                  a complete connection string, used as is (no database in it)
#   MONGO_HOST MONGO_PORT MONGO_USER MONGO_PASSWORD   used to build the URI when MONGO_URI is empty
#   MONGO_DATABASE                             target database (default @@DATASET@@)
# Needs mongoimport + mongosh on this machine; `uv run generator load mongodb` runs this inside the compose container.
set -euo pipefail
cd "$(dirname "$0")"
@@ENV@@

DB="${MONGO_DATABASE:-@@DATASET@@}"
if [ -z "${MONGO_URI:-}" ]; then
  HOSTPORT="${MONGO_HOST:-127.0.0.1}:${MONGO_PORT:-27017}"
  if [ -n "${MONGO_USER:-}" ]; then
    MONGO_URI="mongodb://${MONGO_USER}:${MONGO_PASSWORD:-}@${HOSTPORT}/?authSource=admin"   # URL-encode special characters
  else
    MONGO_URI="mongodb://${HOSTPORT}"
  fi
fi
for collection in @@COLLECTIONS@@; do
  mongoimport --uri "$MONGO_URI" --db "$DB" --collection "$collection" --file "$collection.jsonl" --drop
done
mongosh "$MONGO_URI" --quiet --eval "const DB_NAME='$DB'" indexes.js
"""

README = """# @@DATASET@@ - MongoDB

Generated files; do not edit. Regenerate with `uv run generator generate --formats mongodb`.

- `<table>.jsonl` - one flat collection per table (same field names as the CSV/Parquet columns).
- `sales.jsonl` - denormalised: one document per `tab_fato001` row, with `cliente`, `produto`,
  `fabrica`, `organizacional` embedded and the row's `frete` (from `tab_fato002`). `_id` is
  `"<COD_DIA>|<COD_CLIENTE>|<COD_PRODUTO>|<COD_FABRICA>"`.
- Dates use Extended JSON (`{"$date": ...}`), so `mongoimport` stores real `Date` values; whole-number
  measures are written as `50.0` so they import as doubles.

## Load

```bash
docker compose up -d --wait mongo        # from the repo root; skip if you use your own server
uv run generator load mongodb            # runs import.sh inside the container (no mongoimport needed on your machine)
uv run generator load mongodb --host     # runs import.sh with mongoimport/mongosh on your machine, using the .env URL
```

Change the target in the repo-root `.env`: `MONGO_HOST`, `MONGO_PORT`, `MONGO_USER`, `MONGO_PASSWORD`,
`MONGO_DATABASE`, or a complete `MONGO_URI` (e.g. `mongodb+srv://...`) which replaces the first four.
An environment variable of the same name wins over `.env`.
"""


class MongoWriter(JsonWriter):
    name = "mongodb"
    mongo = True

    def __init__(self, root, dataset):
        super().__init__(root, dataset)
        self._dims: dict[str, pa.Table] = {}
        self._lookups: dict[str, tuple[pa.Array, pa.Array]] | None = None

    def write_dimension(self, name: str, table: pa.Table) -> None:
        self._dims[name] = table
        super().write_dimension(name, table)

    # ---- denormalised sales -------------------------------------------------------------------
    def _build_lookups(self) -> dict[str, tuple[pa.Array, pa.Array]]:
        """For each embedded entity: (codes, '"key":{...}' fragments), built once from the small dimensions."""
        d = {name: table.to_pylist() for name, table in self._dims.items()}
        brand = {r["Cod_Marca"]: r for r in d["dim_marca"]}
        category = {r["Cod_Categoria"]: r["Desc_Categoria"] for r in d["dim_categoria"]}

        def entity(key, rows, code_col, shape):
            codes = pa.array([r[code_col] for r in rows], type=pa.string())
            frags = pa.array([f'"{key}":{_dumps(shape(r))}' for r in rows], type=pa.string())
            return codes, frags

        return {
            "COD_CLIENTE": entity("cliente", d["dim_cliente"], "Cod_Cliente", lambda r: {
                "cod": r["Cod_Cliente"], "desc": r["Desc_Cliente"], "cidade": r["Desc_Cidade"],
                "uf": r["Cod_Estado"], "estado": r["Desc_Estado"], "regiao": r["Desc_Regiao"],
                "segmento": r["Desc_Segmento"]}),
            "COD_PRODUTO": entity("produto", d["dim_produto"], "Cod_Produto", lambda r: {
                "cod": r["Cod_Produto"], "desc": r["Desc_Produto"], "tamanho": r["Atr_Tamanho"],
                "sabor": r["Atr_Sabor"], "marca": brand[r["Cod_Marca"]]["Desc_Marca"],
                "categoria": category[brand[r["Cod_Marca"]]["Cod_Categoria"]]}),
            "COD_FABRICA": entity("fabrica", d["dim_fabrica"], "Cod_Fabrica", lambda r: {
                "cod": r["Cod_Fabrica"], "desc": r["Desc_Fabrica"]}),
            "COD_ORGANIZACIONAL": entity("organizacional", d["dim_organizacional"], "Cod_Filho", lambda r: {
                "cod": r["Cod_Filho"], "desc": r["Desc_Filho"], "nivel": r["Nivel"], "cod_pai": r["Cod_Pai"]}),
        }

    def _sales(self, f1: pa.Table, f2: pa.Table) -> str:
        keys = ["COD_DIA", "COD_CLIENTE", "COD_PRODUTO", "COD_FABRICA"]
        f1, f2 = f1.combine_chunks(), f2.combine_chunks()
        if f1.num_rows != f2.num_rows or not all(pc.all(pc.equal(f1[k], f2[k])).as_py() for k in keys):
            raise ValueError("tab_fato001 and tab_fato002 rows are not aligned; cannot embed freight")
        if self._lookups is None:
            self._lookups = self._build_lookups()

        def frag(column: str) -> pa.Array:
            codes, frags = self._lookups[column]
            idx = pc.index_in(f1[column], value_set=codes)
            if idx.null_count:
                raise ValueError(f"{column}: value missing from its dimension")
            return frags.take(idx)

        def measure(source: pa.Table, column: str, out: str) -> pa.Array:
            return join(lit(f',"{out}":'), json_value(source[column], source.schema.field(column), True))

        head = join(lit('{"_id":"'), f1["COD_DIA"], lit("|"), f1["COD_CLIENTE"], lit("|"), f1["COD_PRODUTO"],
                    lit("|"), f1["COD_FABRICA"], lit('","data":'),
                    json_value(f1["DATA_FATO"], f1.schema.field("DATA_FATO"), True),
                    lit(',"cod_dia":'), json_string(f1["COD_DIA"]), lit(","))
        row = join(head, frag("COD_CLIENTE"), lit(","), frag("COD_PRODUTO"), lit(","), frag("COD_FABRICA"),
                   lit(","), frag("COD_ORGANIZACIONAL"),
                   measure(f1, "FATURAMENTO", "faturamento"), measure(f1, "IMPOSTO", "imposto"),
                   measure(f1, "CUSTO_VARIAVEL", "custo_variavel"), measure(f1, "UNIDADE_VENDIDA", "unidades"),
                   measure(f1, "QUANTIDADE_VENDIDA", "quantidade"), measure(f2, "FRETE", "frete"), lit("}"))
        return "\n".join(row.to_pylist()) + "\n"

    def write_facts_year(self, year: int, tables: dict[str, pa.Table]) -> None:
        super().write_facts_year(year, tables)
        if "tab_fato001" in tables and "tab_fato002" in tables:
            mode = "a" if "sales" in self._started else "w"
            self._started.add("sales")
            with open(self.root / "sales.jsonl", mode, encoding="utf-8", newline="\n") as fh:
                fh.write(self._sales(tables["tab_fato001"], tables["tab_fato002"]))

    # ---- helper scripts -------------------------------------------------------------------------
    def close(self) -> None:
        collections = [t for t in LOAD_ORDER if (self.root / f"{t}.jsonl").exists()]
        if (self.root / "sales.jsonl").exists():
            collections.append("sales")

        def write(name: str, text: str) -> None:
            (self.root / name).write_text(text, encoding="utf-8", newline="\n")

        def fill(text: str) -> str:
            return (text.replace("@@ENV@@", ENV_LOADER).replace("@@DATASET@@", self.dataset)
                    .replace("@@COLLECTIONS@@", " ".join(collections)))

        write("import.sh", fill(IMPORT_SH))
        js = [f"// Run with: mongosh <uri> --eval \"const DB_NAME='{self.dataset}'\" indexes.js",
              f"const database = db.getSiblingDB(typeof DB_NAME === 'undefined' ? '{self.dataset}' : DB_NAME);"]
        for table in collections:
            if table in PRIMARY_KEYS:
                spec = ", ".join(f"{c}: 1" for c in PRIMARY_KEYS[table])
                js.append(f"database.{table}.createIndex({{ {spec} }}, {{ unique: true }});")
        if "sales" in collections:
            js += ["database.sales.createIndex({ data: 1 });",
                   'database.sales.createIndex({ "cliente.cod": 1, data: 1 });',
                   'database.sales.createIndex({ "produto.cod": 1, data: 1 });',
                   'database.sales.createIndex({ "cliente.estado": 1, "produto.categoria": 1, data: 1 });']
        write("indexes.js", "\n".join(js) + "\n")
        write("README.md", fill(README))

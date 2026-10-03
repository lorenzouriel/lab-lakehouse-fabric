"""One description of every table: columns, primary keys, foreign keys, load order.

Shared by the validator and the SQL / MongoDB writers so they cannot drift apart.
"""
from __future__ import annotations

import pyarrow as pa

from .dims import SCHEMAS as _DIM_SCHEMAS
from .facts import FACT_SCHEMAS
from .timedim import SCHEMA as _TEMPO_SCHEMA

DIMENSION_SCHEMAS: dict[str, pa.Schema] = {**_DIM_SCHEMAS, "dim_tempo": _TEMPO_SCHEMA}
TABLE_SCHEMAS: dict[str, pa.Schema] = {**DIMENSION_SCHEMAS, **FACT_SCHEMAS}

# Parents before children, so the order is valid for FK-checked loads.
LOAD_ORDER = ["dim_categoria", "dim_marca", "dim_produto", "dim_cliente", "dim_fabrica",
              "dim_organizacional", "dim_tempo",
              "tab_fato001", "tab_fato002", "tab_fato003", "tab_fato004", "tab_fato005"]

PRIMARY_KEYS: dict[str, list[str]] = {
    "dim_categoria": ["Cod_Categoria"], "dim_marca": ["Cod_Marca"], "dim_produto": ["Cod_Produto"],
    "dim_cliente": ["Cod_Cliente"], "dim_fabrica": ["Cod_Fabrica"], "dim_organizacional": ["Cod_Filho"],
    "dim_tempo": ["Cod_Dia"],
    "tab_fato001": ["COD_CLIENTE", "COD_PRODUTO", "COD_ORGANIZACIONAL", "COD_FABRICA", "COD_DIA"],
    "tab_fato002": ["COD_CLIENTE", "COD_PRODUTO", "COD_FABRICA", "COD_DIA"],
    "tab_fato003": ["COD_FABRICA", "COD_DIA"],
    "tab_fato004": ["COD_CLIENTE", "COD_PRODUTO", "COD_ORGANIZACIONAL", "COD_DIA"],
    "tab_fato005": ["COD_PRODUTO", "COD_FABRICA", "COD_DIA"],
}

# fact column -> (dimension table, dimension column)
FACT_FOREIGN_KEYS = {
    "COD_DIA": ("dim_tempo", "Cod_Dia"),
    "COD_CLIENTE": ("dim_cliente", "Cod_Cliente"),
    "COD_PRODUTO": ("dim_produto", "Cod_Produto"),
    "COD_FABRICA": ("dim_fabrica", "Cod_Fabrica"),
    "COD_ORGANIZACIONAL": ("dim_organizacional", "Cod_Filho"),
}

# (table, column, referenced table, referenced column)
FOREIGN_KEYS: list[tuple[str, str, str, str]] = [
    ("dim_marca", "Cod_Categoria", "dim_categoria", "Cod_Categoria"),
    ("dim_produto", "Cod_Marca", "dim_marca", "Cod_Marca"),
    ("dim_organizacional", "Cod_Pai", "dim_organizacional", "Cod_Filho"),
] + [(fact, col, *FACT_FOREIGN_KEYS[col])
     for fact in FACT_SCHEMAS for col in FACT_SCHEMAS[fact].names if col in FACT_FOREIGN_KEYS]

MEASURES = {
    "tab_fato001": ["FATURAMENTO", "IMPOSTO", "CUSTO_VARIAVEL", "UNIDADE_VENDIDA", "QUANTIDADE_VENDIDA"],
    "tab_fato002": ["FRETE"], "tab_fato003": ["CUSTO_FIXO"],
    "tab_fato004": ["META_FATURAMENTO"], "tab_fato005": ["META_CUSTO"],
}

# Fact -> the columns it shares with tab_fato001 (its keys are exactly the distinct values there).
DERIVED_FROM_FATO001 = {
    "tab_fato002": ["COD_DIA", "COD_CLIENTE", "COD_PRODUTO", "COD_FABRICA"],
    "tab_fato003": ["COD_DIA", "COD_FABRICA"],
    "tab_fato004": ["COD_DIA", "COD_CLIENTE", "COD_PRODUTO", "COD_ORGANIZACIONAL"],
    "tab_fato005": ["COD_DIA", "COD_PRODUTO", "COD_FABRICA"],
}

# Databricks notebook source
# Lakeflow SDP — Trade medallion com Compatibility Mode (demo_poc)
from pyspark import pipelines as dp
from pyspark.sql import functions as F

ICEBERG_BASE = "s3://gabrielrangel-databricks-externallocation/iceberg"
RAW  = "s3://gabrielrangel-databricks-externallocation/raw/trade/"
META = "s3://gabrielrangel-databricks-externallocation/bronze/_pipeline_meta/schemas"

def _compat(loc):
    # Compatibility Mode: gera cópia Iceberg read-only no local indicado (external location)
    return {
        "delta.universalFormat.enabledFormats": "compatibility",
        "delta.universalFormat.compatibility.location": loc,
    }

# BRONZE — streaming table via Auto Loader (schema explícito p/ simplicidade)
@dp.table(
    name="bronze.bronze_trade",
    comment="Trade bruto ingerido do raw via Auto Loader",
    table_properties=_compat(f"{ICEBERG_BASE}/bronze/bronze_trade"),
)
def bronze_trade():
    schema = ("data_registro DATE, ncm STRING, pais STRING, fluxo STRING, "
              "empresa STRING, valor_fob_usd DOUBLE, peso_kg DOUBLE")
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.schemaLocation", f"{META}/bronze_trade")
        .schema(schema)
        .load(RAW)
        .withColumn("_ingested_at", F.current_timestamp())
        .withColumn("_source_file", F.col("_metadata.file_path"))
    )

# SILVER — streaming table: limpa / valida / normaliza
@dp.table(
    name="silver.silver_trade",
    comment="Trade limpo e validado",
    table_properties=_compat(f"{ICEBERG_BASE}/silver/silver_trade"),
)
def silver_trade():
    return (
        spark.readStream.table("bronze.bronze_trade")
        .filter(F.col("valor_fob_usd") > 0)
        .filter(F.col("fluxo").isin("IMP", "EXP"))
        .withColumn("pais", F.upper(F.col("pais")))
    )

# GOLD — materialized view: agregação por país e fluxo
@dp.materialized_view(
    name="gold.gold_trade_by_country",
    comment="Totais de trade por pais e fluxo",
    table_properties=_compat(f"{ICEBERG_BASE}/gold/gold_trade_by_country"),
)
def gold_trade_by_country():
    return (
        spark.read.table("silver.silver_trade")
        .groupBy("pais", "fluxo")
        .agg(
            F.count("*").alias("qtd_registros"),
            F.sum("valor_fob_usd").alias("total_fob_usd"),
            F.sum("peso_kg").alias("total_peso_kg"),
        )
    )

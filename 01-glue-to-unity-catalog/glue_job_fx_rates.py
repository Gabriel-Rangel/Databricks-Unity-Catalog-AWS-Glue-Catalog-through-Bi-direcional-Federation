"""Glue job (PySpark) — gera cotações de fx sintéticas e registra a tabela
nativa glue_native_db.fx_rates no AWS Glue Data Catalog (PoC federação reversa)."""
import sys
import datetime
import random

from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql.types import StructType, StructField, StringType, DateType, DoubleType

args = getResolvedOptions(sys.argv, ["JOB_NAME"])
sc = SparkContext.getOrCreate()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["JOB_NAME"], args)

random.seed(7)
moedas = ["USD", "EUR", "CNY", "ARS"]
base = {"USD": 5.10, "EUR": 5.50, "CNY": 0.70, "ARS": 0.0055}

rows = []
start = datetime.date(2026, 1, 1)
for d in range(30):  # 30 dias
    dia = start + datetime.timedelta(days=d)
    for m in moedas:
        compra = round(base[m] * (1 + random.uniform(-0.03, 0.03)), 4)
        venda = round(compra * (1 + random.uniform(0.001, 0.02)), 4)
        rows.append((dia, m, float(compra), float(venda)))

schema = StructType([
    StructField("data", DateType()),
    StructField("moeda", StringType()),
    StructField("taxa_compra", DoubleType()),
    StructField("taxa_venda", DoubleType()),
])
df = spark.createDataFrame(rows, schema)

# database glue_native_db já existe (criado pelo admin); aqui só registramos a tabela
(df.write
   .mode("overwrite")
   .option("path", "s3://gabrielrangel-gluecatalog-lake/glue-native/fx_rates")
   .format("parquet")
   .saveAsTable("glue_native_db.fx_rates"))

print("Linhas escritas:", df.count())
job.commit()

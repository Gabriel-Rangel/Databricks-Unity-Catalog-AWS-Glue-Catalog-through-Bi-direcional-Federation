# Databricks notebook source
# MAGIC %md
# MAGIC # Registrar tabelas do Compatibility Mode no Glue/Athena (via boto3)
# MAGIC
# MAGIC Roda o `CREATE EXTERNAL TABLE` no Amazon Athena a partir do Databricks, lendo as
# MAGIC saídas do Compatibility Mode como **Delta** (`table_type='DELTA'`).
# MAGIC
# MAGIC **Pré-requisito:** uma **UC Service Credential** chamada `athena_svc` (ver instruções no fim).
# MAGIC
# MAGIC > O DDL é **uma vez só**: depois de criada, cada execução do pipeline aparece sozinha
# MAGIC > no Athena (ele lê o `_delta_log` ao vivo). Rode de novo só para tabelas novas / mudança de schema.

# COMMAND ----------

# MAGIC %md ### 1. Configuração

# COMMAND ----------

SERVICE_CREDENTIAL = "athena_svc"
REGION   = "us-east-2"
RESULTS  = "s3://gabrielrangel-gluecatalog-lake/athena-results/"
GLUE_DB  = "trade_poc"
ICEBERG_BASE = "s3://gabrielrangel-databricks-externallocation/iceberg"

# (layer, nome_da_tabela)
TABLES = [
    ("bronze", "bronze_trade"),
    ("silver", "silver_trade"),
    ("gold",   "gold_trade_by_country"),
]

# COMMAND ----------

# MAGIC %md ### 2. Credenciais temporárias da AWS via UC Service Credential
# MAGIC O UC vende credenciais temporárias (sem chaves estáticas), governadas pela credential `athena_svc`.
# MAGIC
# MAGIC Chamamos a REST API direto (`/api/2.1/unity-catalog/temporary-service-credentials`) para
# MAGIC não depender da versão do `databricks-sdk` pré-instalada no runtime.

# COMMAND ----------

import time
import requests
import boto3

# Token e host do contexto do próprio notebook
_ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
_host = _ctx.apiUrl().get()
_token = _ctx.apiToken().get()

_resp = requests.post(
    f"{_host}/api/2.1/unity-catalog/temporary-service-credentials",
    headers={"Authorization": f"Bearer {_token}"},
    json={"credential_name": SERVICE_CREDENTIAL},
)
_resp.raise_for_status()
aws = _resp.json()["aws_temp_credentials"]

athena = boto3.client(
    "athena",
    region_name=REGION,
    aws_access_key_id=aws["access_key_id"],
    aws_secret_access_key=aws["secret_access_key"],
    aws_session_token=aws["session_token"],
)
print("boto3 Athena client pronto (creds temporárias do UC via REST).")

# COMMAND ----------

# MAGIC %md #### (Alternativa) Mesma coisa via `databricks-sdk`
# MAGIC Versão original com o SDK. **Requer SDK atualizado** — rode antes, numa célula no topo:
# MAGIC `%pip install --upgrade databricks-sdk` e depois `dbutils.library.restartPython()`.
# MAGIC Use **ou** esta célula **ou** a célula REST acima (não precisa das duas).

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
tc = w.credentials.generate_temporary_service_credential(credential_name=SERVICE_CREDENTIAL)
aws = tc.aws_temp_credentials

athena = boto3.client(
    "athena",
    region_name=REGION,
    aws_access_key_id=aws.access_key_id,
    aws_secret_access_key=aws.secret_access_key,
    aws_session_token=aws.session_token,
)
print("boto3 Athena client pronto (creds temporárias do UC via SDK).")

# COMMAND ----------

# MAGIC %md ### 3. Helper para rodar query no Athena (com polling)

# COMMAND ----------

def athena_run(sql: str, database: str = "default") -> str:
    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": RESULTS},
    )["QueryExecutionId"]
    while True:
        s = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        state = s["State"]
        if state == "SUCCEEDED":
            return qid
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Query {state}: {s.get('StateChangeReason')}\nSQL: {sql[:200]}")
        time.sleep(2)

def athena_rows(qid: str):
    rs = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
    return [[c.get("VarCharValue") for c in r["Data"]] for r in rs]

# COMMAND ----------

# MAGIC %md ### 4. Criar o database e registrar as 3 tabelas (como Delta)

# COMMAND ----------

athena_run(f"CREATE DATABASE IF NOT EXISTS {GLUE_DB}")
print(f"database ok: {GLUE_DB}")

for layer, tbl in TABLES:
    loc = f"{ICEBERG_BASE}/{layer}/{tbl}"
    athena_run(f"DROP TABLE IF EXISTS {tbl}", GLUE_DB)
    athena_run(
        f"CREATE EXTERNAL TABLE {tbl} LOCATION '{loc}' TBLPROPERTIES ('table_type'='DELTA')",
        GLUE_DB,
    )
    print(f"registrada: {GLUE_DB}.{tbl} -> {loc}")

# COMMAND ----------

# MAGIC %md ### 5. Validar — contagens e amostra do gold

# COMMAND ----------

qid = athena_run(
    f"SELECT 'bronze' AS lvl, count(*) AS n FROM {GLUE_DB}.bronze_trade "
    f"UNION ALL SELECT 'silver', count(*) FROM {GLUE_DB}.silver_trade "
    f"UNION ALL SELECT 'gold', count(*) FROM {GLUE_DB}.gold_trade_by_country ORDER BY lvl",
    GLUE_DB,
)
for row in athena_rows(qid):
    print(row)

# COMMAND ----------

qid = athena_run(
    f"SELECT pais, fluxo, qtd_registros, round(total_fob_usd,0) AS fob_usd "
    f"FROM {GLUE_DB}.gold_trade_by_country ORDER BY total_fob_usd DESC LIMIT 6",
    GLUE_DB,
)
display(spark.createDataFrame(athena_rows(qid)[1:], athena_rows(qid)[0]))

# Databricks notebook source
# MAGIC %md
# MAGIC # Exemplo — Managed Table normal com Compatibility Mode
# MAGIC
# MAGIC Cria uma **tabela gerenciada** comum (não é streaming table / MV / pipeline) e ativa o
# MAGIC **Compatibility Mode**, que materializa uma cópia Delta v1 + Iceberg v1 (read-only) no
# MAGIC `compatibility.location` — pronta para Athena/engines externas, igual fizemos no pipeline.
# MAGIC
# MAGIC **Regras do `compatibility.location`:**
# MAGIC - deve estar dentro de uma **external location registrada** (aqui: `demo-iceberg`);
# MAGIC - **um path por tabela** e o destino deve **existir e estar vazio**.

# COMMAND ----------

# MAGIC %md ### 1. Criar a managed table com Compatibility Mode (no CREATE, via CTAS)

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE demo_poc.gold.dim_pais_resumo
# MAGIC TBLPROPERTIES (
# MAGIC   'delta.universalFormat.enabledFormats' = 'compatibility',
# MAGIC   'delta.universalFormat.compatibility.location' = 's3://gabrielrangel-databricks-externallocation/iceberg/gold/dim_pais_resumo',
# MAGIC   'delta.universalFormat.compatibility.targetRefreshInterval' = '0 MINUTES'   -- managed table: checa após cada commit
# MAGIC ) AS
# MAGIC SELECT
# MAGIC   pais,
# MAGIC   count(*)              AS qtd_registros,
# MAGIC   sum(valor_fob_usd)    AS total_fob_usd,
# MAGIC   sum(peso_kg)          AS total_peso_kg
# MAGIC FROM demo_poc.silver.silver_trade
# MAGIC GROUP BY pais;

# COMMAND ----------

# MAGIC %md
# MAGIC > Se der erro de column mapping, adicione `'delta.columnMapping.mode' = 'name'` às TBLPROPERTIES.
# MAGIC >
# MAGIC > **Alternativa — habilitar numa tabela já existente:**
# MAGIC > ```sql
# MAGIC > ALTER TABLE demo_poc.gold.dim_pais_resumo SET TBLPROPERTIES (
# MAGIC >   'delta.universalFormat.enabledFormats' = 'compatibility',
# MAGIC >   'delta.universalFormat.compatibility.location' = 's3://.../iceberg/gold/dim_pais_resumo'
# MAGIC > );
# MAGIC > ```

# COMMAND ----------

# MAGIC %md ### 2. Conferir que o Compatibility Mode está ativo

# COMMAND ----------

# MAGIC %sql
# MAGIC SHOW TBLPROPERTIES demo_poc.gold.dim_pais_resumo;

# COMMAND ----------

# MAGIC %md ### 3. Ler a tabela no Databricks (continua sendo Delta gerenciada normal)

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM demo_poc.gold.dim_pais_resumo ORDER BY total_fob_usd DESC LIMIT 10;

# COMMAND ----------

# MAGIC %md
# MAGIC ### 4. (Opcional) Forçar a geração dos metadados de compatibilidade
# MAGIC A geração é assíncrona; para disparar manualmente:

# COMMAND ----------

# MAGIC %sql
# MAGIC REFRESH TABLE demo_poc.gold.dim_pais_resumo;

# COMMAND ----------

# MAGIC %md
# MAGIC ### 5. Verificar a saída no S3 (cópia Delta+Iceberg)
# MAGIC No path do `compatibility.location` devem aparecer `_delta_log/` e `metadata/` (Iceberg).
# MAGIC Pelo terminal/Athena, depois é só registrar igual ao notebook `athena_register_glue`:
# MAGIC ```sql
# MAGIC CREATE EXTERNAL TABLE dim_pais_resumo
# MAGIC LOCATION 's3://gabrielrangel-databricks-externallocation/iceberg/gold/dim_pais_resumo'
# MAGIC TBLPROPERTIES ('table_type'='DELTA');
# MAGIC ```

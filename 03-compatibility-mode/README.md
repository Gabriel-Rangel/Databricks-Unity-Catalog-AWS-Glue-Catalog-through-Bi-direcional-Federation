# 03 — Compatibility Mode (pré-requisito para expor UC no Glue)

O **Compatibility Mode** gera uma cópia **read-only** das tabelas do Unity Catalog (Delta v1 +
Iceberg v1) num local de S3 (uma **external location**), tornando-as legíveis por engines externas.
É o que permite expor **streaming tables, materialized views e managed tables** do UC — justamente
os tipos que o **UniForm comum não cobre** — e é **pré-requisito** da federação **02 (UC → Glue)** e
do acesso nativo **04 (Athena)**.

## Conteúdo

- **`medallion_pipeline.py`** — pipeline **Lakeflow SDP (serverless, Python)** com arquitetura
  medallion (bronze streaming table via Auto Loader → silver streaming table → gold materialized
  view). **Todas as tabelas** têm Compatibility Mode escrevendo a cópia Iceberg em
  `…/iceberg/{layer}/{tabela}/`.
- **`compatibility_mode_managed_table.py`** — exemplo de **tabela gerenciada comum** (fora de
  pipeline) com Compatibility Mode ativado via `TBLPROPERTIES`.

## Como ativar (resumo)

```sql
-- em CREATE (CTAS) ou ALTER, numa tabela gerenciada / ST / MV
TBLPROPERTIES (
  'delta.universalFormat.enabledFormats' = 'compatibility',
  'delta.universalFormat.compatibility.location' = 's3://<bucket>/iceberg/<layer>/<tabela>',
  'delta.universalFormat.compatibility.targetRefreshInterval' = '0 MINUTES'
)
```

## Regras importantes

- O `compatibility.location` deve estar **dentro de uma external location registrada**, ser **um
  path por tabela** e estar **vazio** no momento da ativação.
- É uma **cópia completa** dos dados (custo de storage proporcional); a sincronização é incremental.
- **Streaming tables / materialized views** só são expostas via Compatibility Mode (UniForm não cobre).
- Requer **Unity Catalog**, **DBR 16.1+** e (para ST/MV) um **pipeline SDP**.

Doc oficial: https://docs.databricks.com/aws/en/external-access/compatibility-mode

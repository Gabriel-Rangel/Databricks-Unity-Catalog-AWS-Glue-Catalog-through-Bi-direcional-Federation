# 01 · AWS Glue → Unity Catalog — federar uma tabela NATIVA do Glue no UC (Lakehouse/HMS federation, federação primária)

Objetivo: uma tabela **nativa do AWS Glue Data Catalog** (criada por um **Glue job**) ser
**federada para dentro do Unity Catalog** e consultável no Databricks. É a **Hive Metastore
federation para AWS Glue** (GA): IAM role self-assuming → **service credential** no UC →
`CREATE CONNECTION TYPE glue` → **external location (read-only)** dos dados → `CREATE FOREIGN CATALOG`.

> Direção **oposta** à federação Glue→UC (que expõe tabelas do UC no Glue). Aqui o **UC lê o Glue**.
> Narrativa: tabela de **cotações de fx** nativa do Glue, depois consultável (e combinável com o trade).

## Valores do ambiente

| Item | Valor |
|---|---|
| Conta AWS / Região | `111122223333` / `us-east-2` (perfil `databricks-sandbox`) |
| Workspace (profile CLI) | `demo` (`https://<WORKSPACE_HOST>`) |
| Bucket de dados | `s3://gabrielrangel-gluecatalog-lake` (prefixo `glue-native/`) |
| Glue database / tabela | `glue_native_db` / `fx_rates` |
| IAM role do Glue job | `gabrielrangel-glue-job-role` |
| IAM role p/ UC (federação) | `gabrielrangel-glue-fed-uc` |
| Service credential (UC) | `glue_federation_cred` |
| Storage credential (UC) | `gluecatalog_lake_cred` |
| External location (UC, read-only) | `gluecatalog-lake-native` → `s3://…/gluecatalog-lake/glue-native` |
| Connection (UC) | `glue_federation` |
| Foreign catalog (UC) | `glue_federated` |

Arquivos nesta pasta: `glue_job_fx_rates.py`, `glue-job-role-*.json`, `glue-fed-uc-role-*.json`.

---

# PARTE A — AWS Glue: job que cria e registra a tabela nativa

## A1. IAM role do Glue job (`gabrielrangel-glue-job-role`)
- Trust: `glue.amazonaws.com` (`glue-job-role-trust.json`).
- Managed `service-role/AWSGlueServiceRole` + inline S3 rw em `gabrielrangel-gluecatalog-lake/*` (`glue-job-role-policy.json`).
```bash
aws iam create-role --role-name gabrielrangel-glue-job-role \
  --assume-role-policy-document file://glue-job-role-trust.json --profile databricks-sandbox
aws iam attach-role-policy --role-name gabrielrangel-glue-job-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole --profile databricks-sandbox
aws iam put-role-policy --role-name gabrielrangel-glue-job-role \
  --policy-name gabrielrangel-glue-job-s3 --policy-document file://glue-job-role-policy.json --profile databricks-sandbox
```

## A2. Database + grants no Lake Formation (conta em modo estrito)
```bash
aws glue create-database --region us-east-2 --profile databricks-sandbox \
  --database-input '{"Name":"glue_native_db"}'

# o job precisa criar a tabela em glue_native_db ...
aws lakeformation grant-permissions --region us-east-2 --profile databricks-sandbox \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::111122223333:role/gabrielrangel-glue-job-role \
  --resource '{"Database":{"Name":"glue_native_db"}}' --permissions CREATE_TABLE DESCRIBE

# ... e o Spark (com --enable-glue-datacatalog) verifica o database `default` no startup → precisa de DESCRIBE
aws lakeformation grant-permissions --region us-east-2 --profile databricks-sandbox \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::111122223333:role/gabrielrangel-glue-job-role \
  --resource '{"Database":{"Name":"default"}}' --permissions DESCRIBE
```
> ⚠️ **Gotcha:** sem `DESCRIBE` no `default`, o job falha logo no início com
> *"Unable to verify existence of default database: Insufficient Lake Formation permission(s)"*.

## A3. Script do job → S3
`glue_job_fx_rates.py` gera fx sintético e faz `saveAsTable("glue_native_db.fx_rates")`
com `option("path","s3://…/glue-native/fx_rates")`. Upload:
```bash
aws s3 cp glue_job_fx_rates.py s3://gabrielrangel-gluecatalog-lake/glue-scripts/ --profile databricks-sandbox
```

## A4. Criar e rodar o job
```bash
aws glue create-job --name gabrielrangel-fx-rates-job --region us-east-2 --profile databricks-sandbox \
  --role arn:aws:iam::111122223333:role/gabrielrangel-glue-job-role \
  --glue-version "5.0" --number-of-workers 2 --worker-type G.1X \
  --command '{"Name":"glueetl","ScriptLocation":"s3://gabrielrangel-gluecatalog-lake/glue-scripts/glue_job_fx_rates.py","PythonVersion":"3"}' \
  --default-arguments '{"--job-language":"python","--TempDir":"s3://gabrielrangel-gluecatalog-lake/glue-temp/","--enable-glue-datacatalog":"true"}'

aws glue start-job-run --job-name gabrielrangel-fx-rates-job --region us-east-2 --profile databricks-sandbox
# aguardar JobRunState=SUCCEEDED (aws glue get-job-run ...)
```
Verificar: `aws glue get-table --database-name glue_native_db --name fx_rates` e `aws s3 ls s3://…/glue-native/fx_rates/`.

---

# PARTE B — Unity Catalog: federar o Glue → UC

## B1. IAM role p/ UC (`gabrielrangel-glue-fed-uc`, self-assuming)
- Trust inicial: UC Master Role + ExternalId placeholder (`glue-fed-uc-role-trust-initial.json`).
- Policy: `glue:Get*` (metadados) + `lakeformation:GetDataAccess` + S3 **read** em `gabrielrangel-gluecatalog-lake/*` (`glue-fed-uc-role-policy.json`).
```bash
aws iam create-role --role-name gabrielrangel-glue-fed-uc \
  --assume-role-policy-document file://glue-fed-uc-role-trust-initial.json --profile databricks-sandbox
aws iam put-role-policy --role-name gabrielrangel-glue-fed-uc \
  --policy-name gabrielrangel-glue-fed-uc-policy --policy-document file://glue-fed-uc-role-policy.json --profile databricks-sandbox
```

## B2. Service credential + finalizar trust
```bash
databricks credentials create-credential --json '{
  "name":"glue_federation_cred","purpose":"SERVICE",
  "aws_iam_role":{"role_arn":"arn:aws:iam::111122223333:role/gabrielrangel-glue-fed-uc"}}' -p demo
# anote aws_iam_role.external_id
```
Edite `glue-fed-uc-role-trust.json` com o **external_id** real (+ self-assuming: a própria role no Principal) e aplique:
```bash
aws iam update-assume-role-policy --role-name gabrielrangel-glue-fed-uc \
  --policy-document file://glue-fed-uc-role-trust.json --profile databricks-sandbox
```

## B3. Storage credential + External location (read-only)
```bash
databricks storage-credentials create --json '{
  "name":"gluecatalog_lake_cred",
  "aws_iam_role":{"role_arn":"arn:aws:iam::111122223333:role/gabrielrangel-glue-fed-uc"}}' -p demo
# o external_id é o MESMO da service credential → o trust já criado serve para as duas

databricks external-locations create gluecatalog-lake-native \
  s3://gabrielrangel-gluecatalog-lake/glue-native gluecatalog_lake_cred --read-only -p demo
```
> ⚠️ **Gotchas:** use **`--read-only`** (federação só lê → basta permissão de leitura no role). E só
> crie a external location **depois** que o job escreveu em `glue-native/` (senão dá
> *"No such file or directory"*).

## B4. Connection (TYPE glue) — SQL
```sql
CREATE CONNECTION glue_federation TYPE glue
OPTIONS (aws_region 'us-east-2', aws_account_id '111122223333', credential 'glue_federation_cred');
```

## B5. Foreign catalog — SQL
```sql
CREATE FOREIGN CATALOG glue_federated USING CONNECTION glue_federation
OPTIONS (authorized_paths 's3://gabrielrangel-gluecatalog-lake/glue-native');
```

## B5.1 — Grants LF de leitura para o role da federação ⚠️ (obrigatório, LF estrito)
A service credential lê os metadados do Glue **assumindo o role `gabrielrangel-glue-fed-uc`**.
Sob Lake Formation estrito, esse role precisa de grants para ver o `default`, o database e a tabela —
senão a consulta falha com *"Required Describe on default"* / tabela não encontrada.
```bash
PRIN=DataLakePrincipalIdentifier=arn:aws:iam::111122223333:role/gabrielrangel-glue-fed-uc
aws lakeformation grant-permissions --region us-east-2 --profile databricks-sandbox --principal $PRIN \
  --resource '{"Database":{"Name":"default"}}' --permissions DESCRIBE
aws lakeformation grant-permissions --region us-east-2 --profile databricks-sandbox --principal $PRIN \
  --resource '{"Database":{"Name":"glue_native_db"}}' --permissions DESCRIBE
aws lakeformation grant-permissions --region us-east-2 --profile databricks-sandbox --principal $PRIN \
  --resource '{"Table":{"DatabaseName":"glue_native_db","TableWildcard":{}}}' --permissions SELECT DESCRIBE
```

## B6. Consultar do Databricks
```sql
SELECT * FROM glue_federated.glue_native_db.fx_rates ORDER BY data, moeda LIMIT 10;
```

### Bônus — join com o trade (mostra o valor da federação)
```sql
SELECT g.pais, g.fluxo, round(g.total_fob_usd,0) AS fob_usd,
       round(g.total_fob_usd * c.media_usd, 0) AS fob_brl
FROM demo_poc.gold.gold_trade_by_country g
CROSS JOIN (SELECT avg(taxa_venda) AS media_usd
            FROM glue_federated.glue_native_db.fx_rates WHERE moeda='USD') c
ORDER BY fob_brl DESC LIMIT 6;
```

---

## Notas
- **Federação é somente leitura** no UC.
- **Lake Formation estrito** afeta **dois** principals: o Glue job (Passo A2: `CREATE_TABLE` + `DESCRIBE default`)
  e o role da federação (Passo B5.1: `DESCRIBE` em `default`/`glue_native_db` + `SELECT` nas tabelas).
- **Mesmo `external_id`** vale para service + storage credential do mesmo role → um trust só.
- **Iceberg-on-Glue federation** é Public Preview; aqui a tabela é Parquet via HMS federation (GA).

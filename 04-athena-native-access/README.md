# Setup — UC Service Credential `athena_svc` (Databricks → Athena/Glue via boto3)

Passo a passo para criar a **IAM role + policy** na AWS e a **UC Service Credential** no
Databricks, permitindo que o notebook `athena_register_glue` chame o Amazon Athena/Glue
a partir do Databricks (serverless) com credenciais temporárias governadas pelo UC.

> **Foco em UI.** Cada passo traz o caminho pela **interface** (Console AWS / UI do Databricks)
> como principal e o comando **CLI** equivalente logo abaixo.

## Valores do ambiente

| Item | Valor |
|---|---|
| Conta AWS | `111122223333` |
| Região | `us-east-2` |
| Perfil AWS CLI | `databricks-sandbox` |
| Workspace Databricks | `https://<WORKSPACE_HOST>` (profile CLI `demo`) |
| Role IAM | `gabrielrangel-athena-svc` |
| Policy IAM | `gabrielrangel-athena-svc-policy` |
| Service Credential (UC) | `athena_svc` |
| Bucket de dados | `s3://gabrielrangel-databricks-externallocation` |
| Bucket de resultados Athena | `s3://gabrielrangel-gluecatalog-lake/athena-results/` |

Arquivos JSON nesta pasta:
- `athena-svc-permissions-policy.json` — permissões (Athena + Glue + S3).
- `athena-svc-trust-policy-initial.json` — trust **inicial** (só UC Master Role), p/ criar a role.
- `athena-svc-trust-policy.json` — trust **final** (self-assuming + External ID real), p/ o Passo 4.

> **Por que duas trusts?** A AWS rejeita uma role que referencia **a si mesma** no trust no
> momento da criação ("Invalid principal"). Então criamos com a trust inicial e, depois que a
> role existe e já temos o **External ID** (gerado no Passo 3), aplicamos a trust final
> (que inclui o *self-assuming*, exigido pelo UC atual).

---

## Passo 1 — Criar a policy de permissões

### Via UI (Console AWS)
1. Console AWS → serviço **IAM** → menu esquerdo **Policies** → **Create policy**.
2. Clique na aba **JSON** e **substitua todo o conteúdo** pelo de `athena-svc-permissions-policy.json`.
3. **Next**.
4. **Policy name:** `gabrielrangel-athena-svc-policy` (descrição opcional).
5. **Create policy**.

### Via CLI
```bash
aws iam create-policy \
  --policy-name gabrielrangel-athena-svc-policy \
  --policy-document file://athena-svc-permissions-policy.json \
  --profile databricks-sandbox
```
ARN resultante: `arn:aws:iam::111122223333:policy/gabrielrangel-athena-svc-policy`.

> Se os buckets usarem **KMS CMK**, adicione um statement com `kms:Decrypt` (dados) e
> `kms:GenerateDataKey` (resultados). Os buckets atuais parecem SSE-S3 (o teste leu sem KMS).

## Passo 2 — Criar a role IAM (com trust inicial) e anexar a policy

### Via UI (Console AWS)
1. IAM → menu esquerdo **Roles** → **Create role**.
2. **Trusted entity type:** selecione **Custom trust policy**.
3. No editor, **cole o conteúdo de `athena-svc-trust-policy-initial.json`** → **Next**.
4. Em **Add permissions**, busque e marque **`gabrielrangel-athena-svc-policy`** → **Next**.
5. **Role name:** `gabrielrangel-athena-svc` → **Create role**.

### Via CLI
```bash
aws iam create-role \
  --role-name gabrielrangel-athena-svc \
  --assume-role-policy-document file://athena-svc-trust-policy-initial.json \
  --profile databricks-sandbox

aws iam attach-role-policy \
  --role-name gabrielrangel-athena-svc \
  --policy-arn arn:aws:iam::111122223333:policy/gabrielrangel-athena-svc-policy \
  --profile databricks-sandbox
```
Role ARN: `arn:aws:iam::111122223333:role/gabrielrangel-athena-svc`.

## Passo 3 — Criar a Service Credential no Unity Catalog

### Via UI (Databricks)
1. No workspace, sidebar **Catalog** → ícone/botão **External Data** → aba **Credentials**.
2. **Create credential**.
3. **Credential type:** `Service credential`.
4. **Credential name:** `athena_svc`.
5. **IAM role ARN:** `arn:aws:iam::111122223333:role/gabrielrangel-athena-svc`.
6. **Create**. O Databricks abre um diálogo mostrando o **External ID** e o **principal**
   (UC Master Role / `unity_catalog_iam_arn`). **Copie os dois** — você usa no Passo 4.

### Via CLI
```bash
databricks credentials create-credential --json '{
  "name": "athena_svc",
  "purpose": "SERVICE",
  "aws_iam_role": {"role_arn": "arn:aws:iam::111122223333:role/gabrielrangel-athena-svc"}
}' -p demo
```
No retorno, anote `aws_iam_role.external_id` e `aws_iam_role.unity_catalog_iam_arn`.

## Passo 4 — Aplicar a trust final (self-assuming + External ID real)

1. Edite `athena-svc-trust-policy.json`:
   - Substitua `SUBSTITUA_PELO_EXTERNAL_ID_MOSTRADO_PELO_DATABRICKS` pelo **External ID** real.
   - Confirme o ARN do **UC Master Role** (1ª entrada do `Principal.AWS`) com o
     `unity_catalog_iam_arn` que o Databricks mostrou; ajuste se diferente.
   - Mantenha a 2ª entrada do `Principal.AWS` (a própria role) — é o **self-assuming**.

### Via UI (Console AWS)
2. IAM → **Roles** → abra `gabrielrangel-athena-svc` → aba **Trust relationships** → **Edit trust policy**.
3. Cole o conteúdo final de `athena-svc-trust-policy.json` → **Update policy**.

### Via CLI
```bash
aws iam update-assume-role-policy \
  --role-name gabrielrangel-athena-svc \
  --policy-document file://athena-svc-trust-policy.json \
  --profile databricks-sandbox
```

## Passo 5 — Validar a credential

### Via UI (Databricks)
- **Catalog → External Data → Credentials → `athena_svc`** → botão **Validate configuration**
  (deve passar em todos os checks).

### Via CLI
```bash
databricks credentials validate-credential --credential-name athena_svc -p demo
```
> Se falhar logo após editar o trust, aguarde ~1 min (propagação do IAM) e tente de novo.

## Passo 6 — Rodar o notebook

Abra `/Users/gabriel.rangel@databricks.com/demo_poc_pipeline/athena_register_glue`
(serverless ou cluster com DBR) e execute. Ele cria o database `trade_poc` no Glue,
registra as 3 tabelas como Delta (apontando para o `compatibility.location` em
`s3://…/externallocation/iceberg/{layer}/{table}`) e valida com contagens + amostra do gold.

---

## Notas

- **Governança:** ao registrar tabelas **nativas** no Glue e ler via Athena, o acesso ocorre
  **fora da governança do UC** (sem RLS/CLS/auditoria do UC). Para leitura governada, use a
  **federação Glue → UC (IRC)** — a "Parte 2 completa".
- **DDL é uma vez só:** com `table_type='DELTA'`, dados de cada execução do pipeline aparecem
  automaticamente no Athena (lê o `_delta_log` ao vivo). Rode o notebook de novo só para
  tabelas novas ou mudança de schema.
- **Service credential ≠ storage credential:** não reutilize a role `gabrielrangel-externallocation`
  (storage credential, trust e finalidade diferentes). Esta role é dedicada a chamadas de
  serviço (Athena/Glue).

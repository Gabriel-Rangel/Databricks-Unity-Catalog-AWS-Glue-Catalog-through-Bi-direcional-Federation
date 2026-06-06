# 02 · Unity Catalog → AWS Glue — expor tabelas do UC no Glue (federação via Iceberg REST Catalog, governada por Lake Formation, federação reversa)

Objetivo: fazer as tabelas do catálogo **`demo_poc`** (UC) aparecerem e serem
**consultáveis dentro do AWS Glue Data Catalog** (Athena/Redshift/EMR/SageMaker), **ao vivo**,
sem cópia, **mantendo a governança do Unity Catalog**. O AWS chama isso de
**"Federate to Databricks Unity Catalog"** — o catálogo federado vive **no Glue** e aponta
**para** o UC, via **Iceberg REST Catalog (IRC)**, com **AWS Lake Formation** aplicando o FGAC.

> Diferença para o que já fizemos: antes registramos tabelas **nativas** no Glue (Athena lia
> direto do S3, **fora** da governança do UC). Agora é **federação governada** — o UC continua
> sendo a fonte de verdade e o Lake Formation vende credenciais temporárias.

## Valores do ambiente

| Item | Valor |
|---|---|
| Workspace Databricks | `https://<WORKSPACE_HOST>` (profile CLI `demo`) |
| Metastore | `gabrielrangel-metastore` (`c4a3de7e-dc40-4243-a11c-e12c581ea5a8`) |
| Catálogo a federar | `demo_poc` (schemas `bronze`, `silver`, `gold`) |
| Conta AWS / Região | `111122223333` / `us-east-2` (perfil `databricks-sandbox`) |
| Token URL (OAuth) | `https://<WORKSPACE_HOST>/oidc/v1/token` |
| Bucket dos dados (vending S3) | `s3://gabrielrangel-databricks-externallocation` |
| Service principal (a criar) | `databricks-demo-fed` |
| Secret (Secrets Manager) | `databricks-demo-sp` |
| IAM role (Glue + LF) | `gabrielrangel-glue-fed-databricks` |
| Catálogo federado no Glue | `demo_poc` |

Arquivos JSON nesta pasta:
- `federation-role-policy.json` — permissões (Secrets Manager + S3 vending).
- `federation-role-trust.json` — trust (glue + lakeformation).

> **Referência:** já existem 2 conexões deste tipo na conta (`sdge-databricks-uc-rest`,
> `sean-databricks-glue-federation`) — o padrão já roda aqui, então é só replicar para o `demo_poc`.

---

# PARTE A — No Databricks / Unity Catalog

## A1. Habilitar External Data Access no metastore  ⚠️ (hoje está **desligado**)

### Via UI
1. **Catalog** → ícone de engrenagem do metastore (ou **Settings → Catalog/Metastore**).
2. Em **Details**, marque **External data access** → **Save**.

### Via CLI
```bash
databricks api patch /api/2.1/unity-catalog/metastores/c4a3de7e-dc40-4243-a11c-e12c581ea5a8 \
  --json '{"external_access_enabled": true}' -p demo
```

## A2. Criar o Service Principal + segredo OAuth

### Via UI
1. **Settings** → **Identity and access** → **Service principals** → **Add service principal**.
   Nome: `databricks-demo-fed`.
2. Abra o SP → aba **Secrets** (OAuth) → **Generate secret**.
   **Copie** o **Client ID** (Application ID) e o **Secret** (só aparece uma vez).

### Via CLI
```bash
# cria o SP
databricks service-principals create --display-name databricks-demo-fed -p demo
# pegue o "id" retornado e gere o secret OAuth (M2M):
databricks service-principal-secrets create <SP_ID> -p demo
```
Guarde **Client ID** e **Secret**.

## A3. Conceder permissões ao SP (incl. `EXTERNAL USE SCHEMA`)

No SQL Editor / notebook (substitua `<CLIENT_ID>` pelo Application ID do SP):
```sql
GRANT USE CATALOG        ON CATALOG demo_poc TO `<CLIENT_ID>`;
GRANT USE SCHEMA         ON CATALOG demo_poc TO `<CLIENT_ID>`;
GRANT SELECT             ON CATALOG demo_poc TO `<CLIENT_ID>`;
GRANT EXTERNAL USE SCHEMA ON CATALOG demo_poc TO `<CLIENT_ID>`;
```
> `EXTERNAL USE SCHEMA` é o privilégio que libera o acesso externo via IRC. Concedido no
> catálogo, vale para todos os schemas/tabelas.

## A4. Anotar para a Parte B
- Workspace URL, **Token URL** (`…/oidc/v1/token`), **Client ID**, **Secret**, catálogo `demo_poc`.

---

# PARTE B — Na AWS (Lake Formation + Glue)

> Pré-requisitos IAM de quem executa: ser **data lake admin** do Lake Formation (você é) +
> permissões de Glue/Secrets/IAM. Doc oficial:
> https://docs.aws.amazon.com/lake-formation/latest/dg/catalog-federation-databricks.html

## B1. Guardar o Secret no AWS Secrets Manager

### Via UI
1. Console **Secrets Manager** (us-east-2) → **Store a new secret** → **Other type of secret**.
2. Em **Key/value**, chave **`USER_MANAGED_CLIENT_APPLICATION_CLIENT_SECRET`**, valor = o **Secret** do SP.
3. Nome do secret: **`databricks-demo-sp`** → **Store**.

### Via CLI
```bash
aws secretsmanager create-secret \
  --name databricks-demo-sp \
  --secret-string '{"USER_MANAGED_CLIENT_APPLICATION_CLIENT_SECRET":"<SECRET_DO_SP>"}' \
  --region us-east-2 --profile databricks-sandbox
```

## B2. Criar a IAM role (Glue + Lake Formation) com a policy

O Console do Lake Formation usa **uma única role** com as duas finalidades (ler o secret e
fazer vending de S3). Por isso a trust permite `glue` **e** `lakeformation`.

### Via UI
1. **IAM → Policies → Create policy → JSON** → cole `federation-role-policy.json` →
   nome `gabrielrangel-glue-fed-databricks-policy`.
2. **IAM → Roles → Create role → Custom trust policy** → cole `federation-role-trust.json` →
   anexe a policy acima → nome **`gabrielrangel-glue-fed-databricks`**.

### Via CLI
```bash
aws iam create-policy --policy-name gabrielrangel-glue-fed-databricks-policy \
  --policy-document file://federation-role-policy.json --profile databricks-sandbox

aws iam create-role --role-name gabrielrangel-glue-fed-databricks \
  --assume-role-policy-document file://federation-role-trust.json --profile databricks-sandbox

aws iam attach-role-policy --role-name gabrielrangel-glue-fed-databricks \
  --policy-arn arn:aws:iam::111122223333:policy/gabrielrangel-glue-fed-databricks-policy \
  --profile databricks-sandbox
```

## B3. Criar o catálogo federado (conexão + registro + catálogo)

### Via UI (Lake Formation — workflow único, recomendado)
1. Console **Lake Formation** (us-east-2) → menu **Catalogs** → **Create catalog**.
2. **Choose data source:** selecione **Databricks**.
3. **Set catalog details:**
   - **Catalog details** → nome do catálogo federado no Glue: **`demo_poc`**;
     **Databricks catalog**: **`demo_poc`**.
   - **Connection details** → **Create new connection**:
     - Connection name: `databricks-demo-fed-conn`
     - Workspace URL: `https://<WORKSPACE_HOST>`
     - Authentication: **OAuth2**
     - Token URL: `https://<WORKSPACE_HOST>/oidc/v1/token`
     - OAuth2 Client ID: **\<CLIENT_ID do SP\>**
     - Secret: **AWS Secrets Manager** → `databricks-demo-sp`
     - Token URL Scope: **`all-apis`**
   - **Registration details** → IAM role: **`gabrielrangel-glue-fed-databricks`**.
4. **Test connection** (indisponível se conectar via Amazon VPC) → **Next** → **Create catalog**.

### Via CLI (equivalente, 3 comandos)
```bash
# 1) Conexão Glue
aws glue create-connection --region us-east-2 --profile databricks-sandbox --connection-input '{
  "Name": "databricks-demo-fed-conn",
  "ConnectionType": "DATABRICKSICEBERGRESTCATALOG",
  "ConnectionProperties": {
    "INSTANCE_URL": "https://<WORKSPACE_HOST>",
    "ROLE_ARN": "arn:aws:iam::111122223333:role/gabrielrangel-glue-fed-databricks"
  },
  "AuthenticationConfiguration": {
    "AuthenticationType": "OAUTH2",
    "OAuth2Properties": {
      "OAuth2GrantType": "CLIENT_CREDENTIALS",
      "TokenUrl": "https://<WORKSPACE_HOST>/oidc/v1/token",
      "OAuth2ClientApplication": {"UserManagedClientApplicationClientId": "<CLIENT_ID>"},
      "TokenUrlParametersMap": {"scope": "all-apis"}
    },
    "SecretArn": "arn:aws:secretsmanager:us-east-2:111122223333:secret:databricks-demo-sp-XXXXXX"
  }
}'

# 2) Registrar a conexão no Lake Formation
aws lakeformation register-resource --region us-east-2 --profile databricks-sandbox \
  --resource-arn arn:aws:glue:us-east-2:111122223333:connection/databricks-demo-fed-conn \
  --role-arn arn:aws:iam::111122223333:role/gabrielrangel-glue-fed-databricks \
  --with-federation --with-privileged-access

# 3) Criar o catálogo federado no Glue
aws glue create-catalog --region us-east-2 --profile databricks-sandbox \
  --name demo_poc \
  --catalog-input '{
    "FederatedCatalog": {
      "Identifier": "demo_poc",
      "ConnectionName": "databricks-demo-fed-conn"
    },
    "CreateDatabaseDefaultPermissions": [],
    "CreateTableDefaultPermissions": []
  }'
```

## B4. Conceder FGAC no Lake Formation aos consumidores

Como o LF está em modo estrito, conceda acesso a quem vai consultar (ex.: seu papel SSO
admin, um analista, etc.):

### Via UI
**Lake Formation → Permissions → Data lake permissions → Grant** → escolha o **principal** →
em **Named Data Catalog resources** selecione o catálogo federado **`demo_poc`** e as
databases/tables → permissões **SELECT / DESCRIBE** → **Grant**.

### Via CLI (exemplo: liberar tudo do catálogo p/ um papel)
```bash
aws lakeformation grant-permissions --region us-east-2 --profile databricks-sandbox \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::111122223333:role/<SEU_PAPEL> \
  --resource '{"Table":{"CatalogId":"111122223333:demo_poc","DatabaseName":"gold","TableWildcard":{}}}' \
  --permissions SELECT DESCRIBE
```
*(Para Redshift, crie também **resource links** apontando para o catálogo federado.)*

---

# PARTE C — Validação (Athena)

1. Console **Athena** (us-east-2) → no seletor de **Catalog/Data source**, o catálogo federado
   **`demo_poc`** deve aparecer (além do `AwsDataCatalog`).
2. Consulte (note o catálogo de 3 partes — `catálogo.database.tabela`):
   ```sql
   SELECT * FROM "demo_poc"."gold"."gold_trade_by_country"
   ORDER BY total_fob_usd DESC LIMIT 10;
   ```
3. Os dados saem ao vivo do UC (via IRC), com credenciais vendidas pelo Lake Formation, e o
   FGAC do LF é aplicado. **Somente leitura** pelo lado AWS.

---

## Notas / troubleshooting

- **`external_access_enabled` precisa estar `true`** (Passo A1) — senão o IRC recusa as chamadas.
- **Apenas tabelas Iceberg no S3** são consultáveis pela federação. As nossas (Compatibility Mode)
  têm metadados Iceberg → ok. Tabelas Delta puras precisariam de UniForm.
- **Test connection** não funciona quando a conexão é via Amazon VPC (limitação documentada).
- **Revogação:** ao dropar tabela no Databricks, o LF **não** revoga permissões automaticamente.
- **Propagação:** se a validação/consulta falhar logo após criar, aguarde ~1 min (propagação IAM) e repita.

---

## Referências (documentação oficial)
- Lake Formation — *Federate to Databricks Unity Catalog* — https://docs.aws.amazon.com/lake-formation/latest/dg/catalog-federation-databricks.html
- Lake Formation — *Creating a federated catalog using an AWS Glue connection* — https://docs.aws.amazon.com/lake-formation/latest/dg/create-fed-catalog-data-source.html
- Lake Formation — *Catalog federation to remote Iceberg catalogs* — https://docs.aws.amazon.com/lake-formation/latest/dg/catalog-federation.html
- UC Iceberg REST Catalog (acesso externo) — https://docs.databricks.com/aws/en/external-access/iceberg
- Habilitar external data access — https://docs.databricks.com/aws/en/external-access/admin
- UniForm / External Iceberg Reads — https://docs.databricks.com/aws/en/delta/uniform
- Compatibility Mode — https://docs.databricks.com/aws/en/external-access/compatibility-mode
- AWS Big Data Blog — *Access Databricks Unity Catalog data using catalog federation in the AWS Glue Data Catalog* — https://aws.amazon.com/blogs/big-data/access-databricks-unity-catalog-data-using-catalog-federation-in-the-aws-glue-data-catalog/

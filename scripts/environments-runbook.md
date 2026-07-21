# Runbook do operador — provisionamento por ambiente (Sprint 3)

> Rodável para **dev E prod** (isolados: nunca compartilham KEK, secret nem serviço).
> Todo passo é **idempotente** ou **1× por ambiente**. Nada aqui derruba um deploy
> em andamento — os grants são best-effort; o único **gate** é o smoke de entrega
> do alerta (03.7), que é intencional.

## Ordem (por ambiente `<env>` ∈ {dev, prod})

### 1. Provisionar a infra versionada — `setup-gcp.sh` (idempotente)

```bash
bash scripts/setup-gcp.sh <env>
```

Cria/garante, sem duplicar:

- **KEK no Cloud KMS** (Req. 1 / 03.1): keyring `auditoria-<env>` + CryptoKey
  `omie-encryption-kek`, e o grant per-key `cloudkms.cryptoKeyEncrypterDecrypter`
  à SA de runtime. Imprime o `KEK_KMS_KEY_NAME` (resource path — **não** é segredo).
- **Secrets do canal de alerta** (Req. 4 / 03.7): `alert-webhook-url-<env>` e
  `alert-email-to-<env>` (container + versão **vazia** semeada + `secretAccessor`
  per-secret à SA de runtime).

> ⚠️ Rode **antes** do 1º deploy: o `--update-secrets ...:latest` dos workflows
> falha se o secret não tiver versão.

### 2. Configurar o canal de plantão real (valor não versionado)

Adicione o VALOR de **pelo menos um** canal entregável (senão o serviço é
**fail-closed** e nem sobe):

```bash
printf 'https://hooks.slack.com/services/XXX' \
  | gcloud secrets versions add alert-webhook-url-<env> --data-file=- --project=liberdade-assessoria
# (e-mail só entrega se ALERT_SMTP_HOST também estiver setado no serviço;
#  o webhook é o canal deliverable por padrão)
```

O destino é **sempre um endereço COMPARTILHADO da equipe de plantão** (grupo,
nunca uma pessoa — fator ônibus).

### 3. Bootstrap do smoke de ENTREGA (03.7) — admin de monitoração

O job `smoke-alert` (pós-deploy) **prova** a entrega: loga como um admin
dedicado e dispara o gatilho sintético da 03.6
(`POST /api/v1/system/alert-test`). Se a notificação não chegar (`delivered=false`),
o pipeline **reprova**.

1. **Crie um admin DEDICADO de monitoração** (perfil admin, senha forte,
   credenciais **não** versionadas — mesmo padrão do admin inicial):

   ```bash
   # exemplo — ajuste ao mecanismo de criação de usuário do ambiente
   SEED_ADMIN_EMAIL=smoke-monitor@hologram.com.br \
   SEED_ADMIN_PASSWORD='<senha-forte>' \
     <rodar o job/rotina de criação de usuário admin>
   ```

2. No GitHub → **Settings → Environments → `<development|production>` → Secrets**:

   | Secret | Valor |
   | --- | --- |
   | `SMOKE_ADMIN_EMAIL` | e-mail do admin de monitoração |
   | `SMOKE_ADMIN_PASSWORD` | senha dele (nunca no repo/log) |

3. Confirme a **Variable** `API_URL_<DEV\|PROD>` no mesmo Environment (base
   pública da API — a mesma que o build do web consome).

### 4. Deploy

- **dev:** automático no push para `main` (paths de `apps/**`), ou
  `workflow_dispatch`.
- **prod:** **só manual** (`workflow_dispatch` / tag `v*.*.*`) — gate por processo
  (GitHub Free não tem required reviewers). Remova o `if: false`/guard de
  `deploy-prod.yml` só quando a infra prod existir.

O deploy roda as **migrations** (job dedicado, antes da API) com paridade de
secrets (KEK inclusive — o backfill de cripto precisa do unwrap). Em seguida o
`smoke-alert` prova a entrega do alerta e o `Register runtime invariant` confere
(read-only) `--min-instances>=1` + `--no-cpu-throttling` (o heartbeat depende
disso; custo pendente de aprovação — ver CLAUDE.md §10).

## Verificação rápida

- [ ] `setup-gcp.sh <env>` rodou sem erro fatal; `KEK_KMS_KEY_NAME` impresso.
- [ ] Pelo menos um canal de alerta com valor real (`gcloud secrets versions list alert-webhook-url-<env>`).
- [ ] Admin de monitoração criado + `SMOKE_ADMIN_EMAIL/PASSWORD` no Environment.
- [ ] `API_URL_<env>` setada no Environment.
- [ ] Deploy verde, com `smoke-alert` provando `delivered=true`.

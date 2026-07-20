#!/bin/bash
# Provisiona a infra de 1ª vez POR AMBIENTE que o deploy assume já existir.
# Rode UMA vez por ambiente (dev e prod são isolados) ANTES do primeiro deploy
# que a consome. Provisiona (Sprint 3):
#   - Req. 1 / INFRA 03.1: KEK no Cloud KMS (envelope encryption). O backend
#     (03.3/03.4) faz wrap/unwrap das DEKs por cliente contra ela; nunca sai do KMS.
#   - Req. 4 / INFRA 03.7: secrets do canal de alerta (ALERT_WEBHOOK_URL/
#     ALERT_EMAIL_TO) + o Cloud Run Job do alerta SINTÉTICO (gate do smoke).
#
# ⚠️ ORDEM IMPORTA: rode este script ANTES do próximo deploy — o `--update-secrets
#    ...:latest` do workflow FALHA se o secret não tiver versão (semeamos vazia).
#    Depois adicione o VALOR real do canal (≥1) — instruções impressas no fim.
#
# Idempotente — pode rodar N vezes:
#   - keyring/CryptoKey do KMS NÃO podem ser deletados; criamos só se não existirem
#     (guard por `describe`), então re-rodar não falha nem duplica.
#   - secrets/Job: guard por `describe`; `add-iam-policy-binding` é no-op se já existe.
#
# Menor privilégio: a SA de RUNTIME recebe cloudkms.cryptoKeyEncrypterDecrypter e
# secretmanager.secretAccessor SOMENTE nos recursos (per-key / per-secret), nunca
# no projeto. A SA de deploy NÃO recebe acesso de runtime aqui.
#
# Pré-requisitos:
#   - gcloud autenticado com cloudkms.admin + secretmanager.admin no projeto.
#   - A SA de runtime (auditoria-api-sa) já criada (grant_cicd_iam.sh).
#   - APIs cloudkms/secretmanager habilitadas (o script tenta; best-effort).
#
# Uso:
#   bash scripts/setup-gcp.sh <dev|prod>

set -euo pipefail

# ------------------------------------------------------------------
# Args / config
# ------------------------------------------------------------------
ENV="${1:-}"
if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
  echo "::error:: uso: bash scripts/setup-gcp.sh <dev|prod>" >&2
  exit 2
fi

PROJECT_ID="${PROJECT_ID:-liberdade-assessoria}"
REGION="${REGION:-southamerica-east1}"
AR_HOST="${AR_HOST:-southamerica-east1-docker.pkg.dev}"
AR_REPO="${AR_REPO:-auditoria}"

# Tag da imagem da API por ambiente (espelha os workflows: dev usa :dev, prod
# também atualiza :latest). O Job sintético é (re)apontado a cada deploy.
if [[ "$ENV" == "dev" ]]; then IMAGE_TAG="dev"; else IMAGE_TAG="latest"; fi
API_IMAGE="${AR_HOST}/${PROJECT_ID}/${AR_REPO}/auditoria-api:${IMAGE_TAG}"

# Keyring sufixado por ambiente → dev e prod NUNCA compartilham KEK.
KEYRING="${KEYRING:-auditoria-${ENV}}"
# A KEK (Key Encryption Key) que embrulha as DEKs por cliente. Nome estável por
# ambiente; a rotação da KEK é decisão operacional (fora do escopo da Sprint 3).
KEK_KEY="${KEK_KEY:-omie-encryption-kek}"

# SA de RUNTIME que o serviço da API e o job de migração usam (mesma SA nos dois
# — a paridade serviço×job depende disso: o backfill 03.4 roda no job e precisa
# do unwrap). Prod pode, no futuro, ter uma SA dedicada — sobrescreva RUNTIME_SA.
RUNTIME_SA="${RUNTIME_SA:-auditoria-api-sa@${PROJECT_ID}.iam.gserviceaccount.com}"

KEK_KMS_KEY_NAME="projects/${PROJECT_ID}/locations/${REGION}/keyRings/${KEYRING}/cryptoKeys/${KEK_KEY}"

echo "=== setup-gcp (${ENV}) — projeto ${PROJECT_ID} / região ${REGION} ==="
echo "    keyring:     ${KEYRING}"
echo "    KEK:         ${KEK_KEY}"
echo "    runtime SA:  ${RUNTIME_SA}"
echo ""

# ------------------------------------------------------------------
# 0. Habilitar APIs necessárias (best-effort — não derruba se já habilitadas)
# ------------------------------------------------------------------
echo "--- Habilitando cloudkms + secretmanager (best-effort) ---"
gcloud services enable cloudkms.googleapis.com secretmanager.googleapis.com \
  --project="$PROJECT_ID" --quiet \
  || echo "::warning:: não foi possível habilitar as APIs (talvez já estejam / sem permissão)"

# ------------------------------------------------------------------
# 1. Keyring (idempotente via describe-guard — keyring não é deletável)
# ------------------------------------------------------------------
echo ""
echo "--- Keyring ${KEYRING} ---"
if gcloud kms keyrings describe "$KEYRING" \
     --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "  já existe — ok"
else
  echo "  criando…"
  gcloud kms keyrings create "$KEYRING" \
    --location="$REGION" --project="$PROJECT_ID" --quiet
fi

# ------------------------------------------------------------------
# 2. CryptoKey simétrica ENCRYPT_DECRYPT (idempotente via describe-guard)
# ------------------------------------------------------------------
echo ""
echo "--- CryptoKey ${KEK_KEY} (purpose=encryption / symmetric) ---"
if gcloud kms keys describe "$KEK_KEY" \
     --keyring="$KEYRING" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "  já existe — ok"
else
  echo "  criando…"
  # --purpose=encryption → chave simétrica ENCRYPT_DECRYPT (default).
  # Sem rotação automática: a KEK é estável; rotacioná-la é decisão operacional
  # (Req. 1 declara isso fora de escopo). A rotação das DEKs é feita no app.
  gcloud kms keys create "$KEK_KEY" \
    --keyring="$KEYRING" --location="$REGION" --project="$PROJECT_ID" \
    --purpose=encryption --quiet
fi

# ------------------------------------------------------------------
# 3. IAM per-key — MENOR PRIVILÉGIO
#    A SA de runtime só pode encrypt/decrypt COM esta chave; nunca exporta a KEK
#    (não existe role/permissão que "baixe" o material — só wrap/unwrap no KMS).
#    Binding no RECURSO da chave, jamais no projeto.
# ------------------------------------------------------------------
echo ""
echo "--- IAM: ${RUNTIME_SA} → cloudkms.cryptoKeyEncrypterDecrypter (per-key) ---"
if gcloud iam service-accounts describe "$RUNTIME_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud kms keys add-iam-policy-binding "$KEK_KEY" \
    --keyring="$KEYRING" --location="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/cloudkms.cryptoKeyEncrypterDecrypter" \
    --quiet >/dev/null \
    && echo "  concedido (ou já existia)" \
    || echo "::warning:: falha ao conceder o binding — verifique permissão de cloudkms.admin"
else
  echo "::warning:: SA ${RUNTIME_SA} não existe ainda — rode grant_cicd_iam.sh / crie a SA e re-execute."
fi

# ------------------------------------------------------------------
# 4. Canais de alerta (Sprint 3, Req. 4 / INFRA 03.7) — Secret Manager
#    ALERT_WEBHOOK_URL / ALERT_EMAIL_TO apontam para um endereço COMPARTILHADO
#    da equipe de plantão (nunca uma pessoa). O VALOR real (URL/e-mail do canal)
#    é credencial de operação — NÃO versionada no repo; o operador adiciona a
#    versão. Aqui criamos só o CONTAINER + secretAccessor per-secret e semeamos
#    uma versão VAZIA (pra o `--update-secrets ...:latest` do deploy resolver).
#    Fail-closed: se AMBOS ficarem vazios, o app RECUSA subir (guarda no backend).
# ------------------------------------------------------------------
ALERT_WEBHOOK_SECRET="alert-webhook-url-${ENV}"
ALERT_EMAIL_SECRET="alert-email-to-${ENV}"

ensure_alert_secret() {
  local name="$1"
  echo "--- Secret ${name} ---"
  if gcloud secrets describe "$name" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "  container já existe — ok"
  else
    echo "  criando container…"
    gcloud secrets create "$name" \
      --replication-policy=automatic --project="$PROJECT_ID" --quiet
  fi
  # Semeia UMA versão vazia se não houver nenhuma — assim `:latest` resolve no
  # deploy mesmo antes de o operador colocar o valor real. Vazio ⇒ "canal não
  # configurado" (o fail-closed do backend cobre o caso de AMBOS vazios).
  if [ -z "$(gcloud secrets versions list "$name" \
              --project="$PROJECT_ID" --format='value(name)' --limit=1 2>/dev/null)" ]; then
    printf '' | gcloud secrets versions add "$name" --data-file=- \
      --project="$PROJECT_ID" --quiet >/dev/null
    echo "  versão vazia semeada (operador deve adicionar o valor REAL do canal)"
  fi
  # secretAccessor PER-SECRET à SA de runtime (menor privilégio).
  if gcloud iam service-accounts describe "$RUNTIME_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "$name" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="roles/secretmanager.secretAccessor" \
      --project="$PROJECT_ID" --quiet >/dev/null \
      && echo "  secretAccessor concedido (ou já existia)" \
      || echo "::warning:: falha ao conceder secretAccessor em ${name}"
  else
    echo "::warning:: SA ${RUNTIME_SA} não existe — conceda secretAccessor em ${name} depois."
  fi
}

echo ""
echo "=== Canais de alerta (${ENV}) ==="
ensure_alert_secret "$ALERT_WEBHOOK_SECRET"
ensure_alert_secret "$ALERT_EMAIL_SECRET"

# ------------------------------------------------------------------
# 5. Cloud Run Job do alerta SINTÉTICO (o gate do smoke pós-deploy)
#    Roda a MESMA imagem/SA, com os secrets do canal + a KEK. O entrypoint é o
#    CONTRATO com a 03.6: `python -m app.cli.alert_synthetic_check` — dispara um
#    alerta real com nonce e sai !=0 se não entregou. Se o backend nomear o
#    módulo diferente, ajuste o --command/--args (uma linha) e re-rode.
#    Best-effort: se a imagem ainda não foi buildada, avisa e segue (o job é
#    (re)apontado a cada deploy no workflow).
# ------------------------------------------------------------------
SYNTHETIC_JOB="auditoria-api-alert-synthetic-${ENV}"
echo ""
echo "--- Cloud Run Job ${SYNTHETIC_JOB} ---"
if gcloud run jobs describe "$SYNTHETIC_JOB" \
     --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "  já existe — ok (o deploy re-aponta imagem/secrets)"
else
  echo "  criando…"
  gcloud run jobs create "$SYNTHETIC_JOB" \
    --image="$API_IMAGE" \
    --region="$REGION" --project="$PROJECT_ID" \
    --service-account="$RUNTIME_SA" \
    --command="python" \
    --args="-m,app.cli.alert_synthetic_check" \
    --set-env-vars="KEK_KMS_KEY_NAME=${KEK_KMS_KEY_NAME}" \
    --set-secrets="ALERT_WEBHOOK_URL=${ALERT_WEBHOOK_SECRET}:latest,ALERT_EMAIL_TO=${ALERT_EMAIL_SECRET}:latest" \
    --max-retries=0 \
    --quiet \
    || echo "::warning:: não criou ${SYNTHETIC_JOB} (imagem ${API_IMAGE} ainda não existe? faça o 1º build e re-rode)."
fi

# ------------------------------------------------------------------
# 6. Contrato com o backend (03.3/03.6) e o deploy — nomes canônicos
# ------------------------------------------------------------------
echo ""
echo "=== ✓ Provisionamento (${ENV}) concluído ==="
echo ""
echo "1) KMS — injetado nos deploys (NÃO é segredo, é resource path):"
echo "     KEK_KMS_KEY_NAME=${KEK_KMS_KEY_NAME}"
echo ""
echo "2) Alerta — adicione o VALOR real do canal compartilhado (pelo menos UM;"
echo "   fail-closed se ambos vazios). Ex.:"
echo "     printf 'https://hooks.slack.com/...' | gcloud secrets versions add ${ALERT_WEBHOOK_SECRET} --data-file=- --project=${PROJECT_ID}"
echo "     printf 'plantao-adl@hologram.com.br' | gcloud secrets versions add ${ALERT_EMAIL_SECRET} --data-file=- --project=${PROJECT_ID}"
echo "   Os deploys montam ${ALERT_WEBHOOK_SECRET}/${ALERT_EMAIL_SECRET} como"
echo "   ALERT_WEBHOOK_URL/ALERT_EMAIL_TO no serviço da API E nos jobs (paridade)."
echo ""

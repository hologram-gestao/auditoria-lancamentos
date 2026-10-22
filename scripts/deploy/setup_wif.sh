#!/bin/bash
# Provisiona num projeto NOVO o que o deploy pelo GitHub Actions assume existir e que
# até aqui nunca foi versionado (épico 86e3r2egb, subtask 86e3r2egh):
#   - as SAs auditoria-api-sa e auditoria-web-sa (as que o workflow impersona);
#   - o pool do Workload Identity auditoria-github-pool e o provider
#     auditoria-github-provider, com o MESMO issuer, attributeMapping e
#     attributeCondition do projeto antigo (lidos em 20/10/2026 por
#     `gcloud iam workload-identity-pools providers describe`);
#   - o binding roles/iam.workloadIdentityUser das duas SAs para o repositório.
#
# Idempotente: todo passo tem guard por `describe` (ou lista de APIs habilitadas);
# rodar duas vezes não cria nada a mais. NUNCA cria chave de SA: a autenticação é só
# por WIF.
#
# Ordem num projeto novo (scripts/environments-runbook.md, passo 0):
#   setup_wif.sh → grant_cicd_iam.sh → setup-gcp.sh → build das imagens no destino →
#   replay_cloud_run_manifests.sh --apply
#
# Uso (as duas variáveis são OBRIGATÓRIAS e não têm default de propósito: um default
# apontaria para o projeto antigo e criaria o pool no lugar errado):
#   PROJECT_ID=hologramos-512320 PROJECT_NUMBER=114342061812 bash scripts/deploy/setup_wif.sh
#
# Opcionais:
#   API_SA_DISPLAY_NAME / WEB_SA_DISPLAY_NAME  display name das SAs (só na criação;
#       passe os do projeto antigo: gcloud iam service-accounts list --project <antigo>)
#   GITHUB_REPOSITORY  owner/repo autorizado (default hologram-gestao/auditoria-lancamentos)

set -euo pipefail

if [[ -z "${PROJECT_ID:-}" || -z "${PROJECT_NUMBER:-}" ]]; then
  echo "::error:: PROJECT_ID e PROJECT_NUMBER são obrigatórios (sem default: um default" >&2
  echo "          apontaria para o projeto antigo). Ex.:" >&2
  echo "          PROJECT_ID=hologramos-512320 PROJECT_NUMBER=114342061812 bash scripts/deploy/setup_wif.sh" >&2
  exit 2
fi
if [[ ! "$PROJECT_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "::error:: PROJECT_NUMBER tem de ser numérico (recebido: ${PROJECT_NUMBER})." >&2
  exit 2
fi

GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-hologram-gestao/auditoria-lancamentos}"

POOL_ID="auditoria-github-pool"
PROVIDER_ID="auditoria-github-provider"
ISSUER_URI="https://token.actions.githubusercontent.com"
ATTRIBUTE_MAPPING="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.aud=assertion.aud,attribute.ref=assertion.ref,attribute.repository=assertion.repository"
ATTRIBUTE_CONDITION="assertion.repository == \"${GITHUB_REPOSITORY}\""

API_SA_ID="auditoria-api-sa"
WEB_SA_ID="auditoria-web-sa"
API_SA_DISPLAY_NAME="${API_SA_DISPLAY_NAME:-${API_SA_ID}}"
WEB_SA_DISPLAY_NAME="${WEB_SA_DISPLAY_NAME:-${WEB_SA_ID}}"
API_SA="${API_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
WEB_SA="${WEB_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

POOL_NAME="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"
PROVIDER_NAME="${POOL_NAME}/providers/${PROVIDER_ID}"
PRINCIPAL_SET="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository/${GITHUB_REPOSITORY}"

echo "=== setup_wif — projeto ${PROJECT_ID} (${PROJECT_NUMBER}) ==="
echo "    repositório autorizado: ${GITHUB_REPOSITORY}"
echo ""

# ------------------------------------------------------------------
# 0. O número informado é o do projeto informado. O principalSet do binding é montado
#    com o NÚMERO: um número de outro projeto daria um binding que nunca casa.
# ------------------------------------------------------------------
_actual_number="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
if [[ "$_actual_number" != "$PROJECT_NUMBER" ]]; then
  echo "::error:: o projeto ${PROJECT_ID} tem número ${_actual_number}, não ${PROJECT_NUMBER}." >&2
  exit 2
fi

# ------------------------------------------------------------------
# 1. APIs que o WIF usa (iam, a troca de token no STS e a impersonação)
# ------------------------------------------------------------------
echo "--- APIs ---"
# run: o deploy e o replay dos manifestos falam com o Cloud Run assim que o WIF
# existe, e sem a API habilitada reprovariam com o WIF certo.
for _api in iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
            cloudresourcemanager.googleapis.com run.googleapis.com; do
  if [[ -n "$(gcloud services list --enabled --project="$PROJECT_ID" \
                --filter="config.name=${_api}" --format='value(config.name)')" ]]; then
    echo "  ${_api}: já habilitada"
  else
    echo "  ${_api}: habilitando…"
    gcloud services enable "$_api" --project="$PROJECT_ID" --quiet
  fi
done

# ------------------------------------------------------------------
# 2. Service accounts (as mesmas duas do projeto antigo)
# ------------------------------------------------------------------
ensure_sa() {
  local sa_id="$1" display_name="$2"
  local email="${sa_id}@${PROJECT_ID}.iam.gserviceaccount.com"
  echo "--- SA ${email} ---"
  if gcloud iam service-accounts describe "$email" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "  já existe — ok"
  else
    echo "  criando…"
    gcloud iam service-accounts create "$sa_id" \
      --display-name="$display_name" --project="$PROJECT_ID" --quiet
  fi
}

echo ""
ensure_sa "$API_SA_ID" "$API_SA_DISPLAY_NAME"
ensure_sa "$WEB_SA_ID" "$WEB_SA_DISPLAY_NAME"

# ------------------------------------------------------------------
# 3. Pool do Workload Identity (location global)
# ------------------------------------------------------------------
echo ""
echo "--- Pool ${POOL_ID} ---"
if gcloud iam workload-identity-pools describe "$POOL_ID" \
     --location=global --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "  já existe — ok"
else
  echo "  criando…"
  gcloud iam workload-identity-pools create "$POOL_ID" \
    --location=global --project="$PROJECT_ID" \
    --display-name="GitHub Actions" --quiet
fi

# ------------------------------------------------------------------
# 4. Provider OIDC do GitHub, com a condição que restringe ao repositório
# ------------------------------------------------------------------
echo ""
echo "--- Provider ${PROVIDER_ID} ---"
if gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
     --workload-identity-pool="$POOL_ID" --location=global \
     --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "  já existe — conferindo issuer, mapeamento e condição"
else
  echo "  criando…"
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --workload-identity-pool="$POOL_ID" --location=global --project="$PROJECT_ID" \
    --issuer-uri="$ISSUER_URI" \
    --attribute-mapping="$ATTRIBUTE_MAPPING" \
    --attribute-condition="$ATTRIBUTE_CONDITION" \
    --quiet
fi
# Provider já existente (criado à mão ou por outra rodada) tem de ser IGUAL ao esperado:
# sem attribute.repository o principalSet nunca casa, e sem a condição a confiança fica
# mais larga que o repositório.
_describe_provider() {
  gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
    --workload-identity-pool="$POOL_ID" --location=global --project="$PROJECT_ID" \
    --format="$1"
}
_issuer="$(_describe_provider 'value(oidc.issuerUri)')"
_condition="$(_describe_provider 'value(attributeCondition)')"
if [[ "$_issuer" != "$ISSUER_URI" || "$_condition" != "$ATTRIBUTE_CONDITION" ]]; then
  echo "::error:: o provider existente diverge do esperado." >&2
  echo "          issuer:   ${_issuer} (esperado ${ISSUER_URI})" >&2
  echo "          condição: ${_condition} (esperada ${ATTRIBUTE_CONDITION})" >&2
  exit 1
fi
_mapping="$(_describe_provider 'json(attributeMapping)')"
IFS=',' read -r -a _pairs <<< "$ATTRIBUTE_MAPPING"
for _pair in "${_pairs[@]}"; do
  _key="${_pair%%=*}"
  _value="${_pair#*=}"
  if ! grep -qF "\"${_key}\": \"${_value}\"" <<< "$_mapping"; then
    echo "::error:: o attributeMapping do provider existente não tem ${_key}=${_value}." >&2
    exit 1
  fi
done
echo "  issuer, condição e mapeamento conferidos"

# ------------------------------------------------------------------
# 5. workloadIdentityUser: só o repositório pode impersonar as duas SAs
# ------------------------------------------------------------------
ensure_wif_binding() {
  local email="$1"
  echo "--- ${email} ← workloadIdentityUser ---"
  # Sem --filter: o principalSet tem ":" e "/", que a sintaxe de filtro do gcloud lê mal.
  if gcloud iam service-accounts get-iam-policy "$email" --project="$PROJECT_ID" \
       --flatten='bindings[].members' \
       --format='csv[no-heading](bindings.role,bindings.members)' \
     | grep -qxF "roles/iam.workloadIdentityUser,${PRINCIPAL_SET}"; then
    echo "  já existe — ok"
  else
    echo "  concedendo…"
    gcloud iam service-accounts add-iam-policy-binding "$email" \
      --project="$PROJECT_ID" \
      --role="roles/iam.workloadIdentityUser" \
      --member="$PRINCIPAL_SET" \
      --quiet >/dev/null
  fi
}

echo ""
ensure_wif_binding "$API_SA"
ensure_wif_binding "$WEB_SA"

# ------------------------------------------------------------------
# 6. Valores para os secrets do GitHub (nomes de recurso, nenhum é segredo de fato)
# ------------------------------------------------------------------
echo ""
echo "=== ✓ WIF pronto em ${PROJECT_ID} ==="
echo ""
echo "GCP_WORKLOAD_IDENTITY_PROVIDER = ${PROVIDER_NAME}"
echo "GCP_API_SERVICE_ACCOUNT        = ${API_SA}"
echo "GCP_WEB_SERVICE_ACCOUNT        = ${WEB_SA}"
echo ""
echo "Rode scripts/deploy/grant_cicd_iam.sh antes de usar o WIF: a SA recém-criada não tem"
echo "nem resourcemanager.projects.get (vem com run.developer). No corte, grave os três"
echo "valores acima nos secrets do GitHub de mesmo nome."

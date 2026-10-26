#!/bin/bash
# Grant IAM permissions needed for the CI/CD workflows in .github/workflows/.
#
# Idempotent — pode rodar várias vezes. `gcloud projects add-iam-policy-binding`
# é no-op se o binding já existir.
#
# Run once after creating Cloud Run services + WIF + service accounts.
# Re-run after adding new services or roles.
#
# Required: gcloud authenticated. Todo comando passa `--project`, então o projeto
# padrão da máquina não importa (o `add-iam-policy-binding` da SA nela mesma não
# passava e falhava com NOT_FOUND quando o padrão era outro projeto, 86e3r2ehn).
#
# Usage:
#   bash scripts/deploy/grant_cicd_iam.sh
#
# A SA do Cloud Build é descoberta por `gcloud builds get-default-service-account`:
# no hologramos-512320 (criado em 2026) ela é a padrão do Compute
# (`<número>-compute@developer.gserviceaccount.com`) e a legada
# `<número>@cloudbuild.gserviceaccount.com` não existe. CLOUDBUILD_SA=<e-mail> pula a
# descoberta.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-hologramos-512320}"
PROJECT_NUMBER="${PROJECT_NUMBER:-114342061812}"
AR_REPO="${AR_REPO:-auditoria}"
REGION="${REGION:-southamerica-east1}"

API_SA="auditoria-api-sa@${PROJECT_ID}.iam.gserviceaccount.com"
WEB_SA="auditoria-web-sa@${PROJECT_ID}.iam.gserviceaccount.com"

if [[ -z "${CLOUDBUILD_SA:-}" ]]; then
  # Devolve `projects/<número>/serviceAccounts/<e-mail>`; fica só o e-mail.
  # Falha do gcloud (API do Cloud Build desligada, sem permissão) cai no erro
  # orientado abaixo em vez de derrubar o script pelo `set -e`.
  _default_build_sa="$(gcloud builds get-default-service-account \
                         --project="$PROJECT_ID" --format='value(serviceAccountEmail)')" \
    || _default_build_sa=""
  CLOUDBUILD_SA="${_default_build_sa##*/}"
fi
if [[ "$CLOUDBUILD_SA" != *@*.gserviceaccount.com ]]; then
  echo "::error:: não foi possível descobrir a SA do Cloud Build de ${PROJECT_ID} (veio '${CLOUDBUILD_SA}')." >&2
  echo "          Rode 'gcloud builds get-default-service-account --project=${PROJECT_ID}' e passe CLOUDBUILD_SA=<e-mail>." >&2
  exit 2
fi

echo "=== Project: $PROJECT_ID ($PROJECT_NUMBER) ==="
echo ""

# ------------------------------------------------------------------
# Roles para os service accounts impersonados via WIF (rodam o workflow)
# ------------------------------------------------------------------
# - cloudbuild.builds.builder: dispara `gcloud builds submit`. NÃO usar
#                              o `editor` (insuficiente): workflow do
#                              GitHub Actions sobe source pro bucket
#                              <project>_cloudbuild, o que exige
#                              serviceusage.services.use + storage.*
#                              que só vem com `builder`.
# - run.developer:             faz `services update` e `jobs update/execute`.
# - iam.serviceAccountUser:    permite Cloud Run deploy "as" essa SA
#                              (revisão nova precisa rodar com a SA do service).
# - cloudbuild.builds.editor e serviceusage.serviceUsageConsumer: as duas SAs
#                              as têm no projeto antigo, onde o deploy é
#                              verde; concedidas aqui por paridade, porque o
#                              projeto novo nasceu sem elas e o deploy do corte
#                              é a primeira prova (ensaio de 22/10/2026).

GRANT_ROLES=(
  "roles/cloudbuild.builds.builder"
  "roles/cloudbuild.builds.editor"
  "roles/run.developer"
  "roles/serviceusage.serviceUsageConsumer"
)

for SA in "$API_SA" "$WEB_SA"; do
  echo "--- $SA ---"
  for ROLE in "${GRANT_ROLES[@]}"; do
    echo "  + $ROLE"
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:$SA" \
      --role="$ROLE" \
      --condition=None \
      --quiet >/dev/null
  done

  # actAs em si mesma (Cloud Run deploy precisa).
  echo "  + roles/iam.serviceAccountUser (in itself)"
  gcloud iam service-accounts add-iam-policy-binding "$SA" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:$SA" \
    --role="roles/iam.serviceAccountUser" \
    --quiet >/dev/null
done

# ------------------------------------------------------------------
# SA do Cloud Build (a descoberta acima) → escrever no Artifact Registry
# (no projeto novo ela tem `editor` pelos grants automáticos da organização; o
# binding no repositório é o que vale se esses grants forem desligados).
# ------------------------------------------------------------------
echo ""
echo "--- $CLOUDBUILD_SA ---"
echo "  + roles/artifactregistry.writer on $AR_REPO"
gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" \
  --location="$REGION" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:$CLOUDBUILD_SA" \
  --role="roles/artifactregistry.writer" \
  --quiet >/dev/null

# ------------------------------------------------------------------
# SAs do CI → actAs na SA do Cloud Build. O `gcloud builds submit` roda o build
# COMO ela, e quem dispara precisa de iam.serviceAccountUser nela: a legada
# `<número>@cloudbuild` do projeto antigo não exigia, a do Compute exige. Sem
# isso o deploy do corte (23/10/2026) falhou com "caller does not have permission
# to act as service account".
# ------------------------------------------------------------------
for SA in "$API_SA" "$WEB_SA"; do
  echo "  + roles/iam.serviceAccountUser for $SA"
  gcloud iam service-accounts add-iam-policy-binding "$CLOUDBUILD_SA" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:$SA" \
    --role="roles/iam.serviceAccountUser" \
    --quiet >/dev/null
done

# ------------------------------------------------------------------
# Validação rápida
# ------------------------------------------------------------------
echo ""
echo "=== Roles atuais por SA ==="
for SA in "$API_SA" "$WEB_SA"; do
  echo "--- $SA ---"
  gcloud projects get-iam-policy "$PROJECT_ID" \
    --flatten="bindings[].members" \
    --filter="bindings.members:$SA" \
    --format="value(bindings.role)" | sort -u
done

echo ""
echo "=== Quem age como $CLOUDBUILD_SA (iam.serviceAccountUser) ==="
gcloud iam service-accounts get-iam-policy "$CLOUDBUILD_SA" \
  --project="$PROJECT_ID" \
  --flatten="bindings[].members" \
  --filter="bindings.role:roles/iam.serviceAccountUser" \
  --format="value(bindings.members)" | sort -u

echo ""
echo "✓ CI/CD IAM setup completo."

#!/bin/bash
# Grant IAM permissions needed for the CI/CD workflows in .github/workflows/.
#
# Idempotent — pode rodar várias vezes. `gcloud projects add-iam-policy-binding`
# é no-op se o binding já existir.
#
# Run once after creating Cloud Run services + WIF + service accounts.
# Re-run after adding new services or roles.
#
# Required: gcloud authenticated and project set:
#   gcloud config set project liberdade-assessoria
#
# Usage:
#   bash scripts/deploy/grant_cicd_iam.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-liberdade-assessoria}"
PROJECT_NUMBER="${PROJECT_NUMBER:-625547925581}"
AR_REPO="${AR_REPO:-auditoria}"
REGION="${REGION:-southamerica-east1}"

API_SA="auditoria-api-sa@${PROJECT_ID}.iam.gserviceaccount.com"
WEB_SA="auditoria-web-sa@${PROJECT_ID}.iam.gserviceaccount.com"
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

echo "=== Project: $PROJECT_ID ($PROJECT_NUMBER) ==="
echo ""

# ------------------------------------------------------------------
# Roles para os service accounts impersonados via WIF (rodam o workflow)
# ------------------------------------------------------------------
# - cloudbuild.builds.editor: dispara `gcloud builds submit`.
# - run.developer:            faz `services update` e `jobs update/execute`.
# - iam.serviceAccountUser:   permite Cloud Run deploy "as" essa SA
#                             (revisão nova precisa rodar com a SA do service).

GRANT_ROLES=(
  "roles/cloudbuild.builds.editor"
  "roles/run.developer"
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
    --member="serviceAccount:$SA" \
    --role="roles/iam.serviceAccountUser" \
    --quiet >/dev/null
done

# ------------------------------------------------------------------
# Cloud Build default SA → escrever em Artifact Registry
# (já vem com permissões em GCP padrão, mas garantimos por segurança).
# ------------------------------------------------------------------
echo ""
echo "--- $CLOUDBUILD_SA ---"
echo "  + roles/artifactregistry.writer on $AR_REPO"
gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" \
  --location="$REGION" \
  --member="serviceAccount:$CLOUDBUILD_SA" \
  --role="roles/artifactregistry.writer" \
  --quiet >/dev/null

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
echo "✓ CI/CD IAM setup completo."

#!/bin/bash
# .agents-hub/preflight.sh — checagens do STACK do projeto, rodadas antes da sprint.
# Sai != 0 com mensagem clara se faltar algo (nenhum agent trava por ambiente).
# O hub já valida (built-in): git, Claude CLI (PATH_CLAUDE), gh autenticado, repo +
# remoto origin + branch base, arquivos .claude/agents/*.md, CLICKUP_TOKEN válido,
# IDs (team/list/doc), statuses obrigatórios na lista e página de sprint ativa no doc.
# Aqui ficam só as checagens da sua STACK (deps/toolchain). Descomente/edite conforme.
set -uo pipefail
fail() { echo "$1"; exit 1; }

# Exemplos (ative os que fizerem sentido):
# command -v jq >/dev/null || fail "jq ausente — instale jq (os hooks usam)."
# gcloud auth print-access-token >/dev/null 2>&1 || fail "cloud CLI não logado."
# [ -x api/.venv/bin/python ] || fail "venv do backend ausente."
# [ -d web/node_modules ] || fail "node_modules do frontend ausente."

echo "preflight do projeto ok"

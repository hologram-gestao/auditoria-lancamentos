#!/bin/bash
# .agents-hub/preflight.sh — checagens do STACK do projeto, rodadas antes da sprint.
# Sai != 0 com mensagem clara se faltar algo (nenhum agent trava por ambiente).
# O hub já valida (built-in): git, Claude CLI (PATH_CLAUDE), gh autenticado, repo +
# remoto origin + branch base, arquivos .claude/agents/*.md, CLICKUP_TOKEN válido,
# IDs (team/list/doc), statuses obrigatórios na lista e página de sprint ativa no doc.
# Aqui ficam só as checagens da sua STACK (deps/toolchain). Descomente/edite conforme.
set -uo pipefail
fail() { echo "$1"; exit 1; }

# ── Dependência dos hooks do .claude/ ────────────────────────────────────────
# Os hooks leem o payload JSON via `_json.sh`, que usa python3 (e NÃO jq: jq não está
# instalado nesta máquina, e a versão anterior dos hooks falhava ABERTA em silêncio por
# causa disso — `jq ... 2>/dev/null` devolvia vazio e o hook fazia `exit 0`).
command -v python3 >/dev/null || fail "python3 ausente — os hooks do .claude/ dependem dele (ver .claude/hooks/_json.sh)."

# Exemplos (ative os que fizerem sentido):
# gcloud auth print-access-token >/dev/null 2>&1 || fail "cloud CLI não logado."
# [ -x api/.venv/bin/python ] || fail "venv do backend ausente."
# [ -d web/node_modules ] || fail "node_modules do frontend ausente."

echo "preflight do projeto ok"

#!/bin/bash
# .claude/hooks/subagent-stop.sh
# SubagentStop — registra no scratchpad qual subagente terminou.
#
# O payload do SubagentStop traz `agent_type` (nome do agente: Explore, Plan, um agente
# do projeto…) e `agent_id` (uuid). A versão anterior lia `.agent_id` com `jq`, que NÃO
# está instalado: o valor saía vazio e o hook escrevia 45 linhas de
# "### Subagent stop:  — <data>" sem identificar nada. Agora lê sem `jq` (ver _json.sh),
# prefere o NOME ao uuid, e não escreve linha nenhuma quando não consegue identificar —
# registro sem informação só faz o arquivo crescer.

INPUT=$(cat)
source "$(dirname "${BASH_SOURCE[0]}")/_json.sh"

AGENTE=$(json_get "$INPUT" agent_type)
[[ -z "$AGENTE" ]] && AGENTE=$(json_get "$INPUT" agent_id)
[[ -z "$AGENTE" ]] && exit 0

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[[ -z "$REPO_ROOT" ]] && exit 0

SCRATCHPAD="$REPO_ROOT/.claude/memory/scratchpad.md"
mkdir -p "$(dirname "$SCRATCHPAD")"
printf '\n### Subagent stop: %s — %s\n' "$AGENTE" "$(date '+%Y-%m-%d %H:%M:%S')" >> "$SCRATCHPAD"

exit 0

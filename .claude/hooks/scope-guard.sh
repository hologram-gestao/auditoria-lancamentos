#!/bin/bash
# .claude/hooks/scope-guard.sh
# PreToolUse — bloqueia Write/Edit fora do escopo do agent
# AGENT_DENY_PREFIXES: lista separada por vírgula de prefixos proibidos
# Ex: "api/app/,web/app/" bloqueia escrita em ambos

INPUT=$(cat)
source "$(dirname "${BASH_SOURCE[0]}")/_json.sh"
TOOL=$(json_get "$INPUT" tool_name)
FILE_PATH=$(json_get "$INPUT" tool_input.file_path)

# Só aplica para ferramentas de escrita
if [[ "$TOOL" != "Write" && "$TOOL" != "Edit" && "$TOOL" != "MultiEdit" ]]; then
  exit 0
fi

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# Suporta múltiplos prefixos separados por vírgula
DENY_PREFIXES="${AGENT_DENY_PREFIXES:-}"

if [[ -z "$DENY_PREFIXES" ]]; then
  exit 0
fi

IFS=',' read -ra PREFIXES <<< "$DENY_PREFIXES"
for PREFIX in "${PREFIXES[@]}"; do
  PREFIX="${PREFIX// /}"  # remove espaços
  if [[ -n "$PREFIX" && "$FILE_PATH" == ${PREFIX}* ]]; then
    echo "{\"decision\": \"block\", \"reason\": \"Escopo violado: este agent não tem permissão de escrever em '${PREFIX}'\"}"
    exit 2
  fi
done

exit 0
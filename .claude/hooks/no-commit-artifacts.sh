#!/bin/bash
# .claude/hooks/no-commit-artifacts.sh
# PreToolUse (Bash) — bloqueia commitar artefatos de build/ambiente.
# Reflexo da lição: .venv/__pycache__ commitados quebram merges (add/add) e geram
# .pyc root-owned que travam o git. Input: JSON via stdin com tool_input.command.

INPUT=$(cat)
source "$(dirname "${BASH_SOURCE[0]}")/_json.sh"
CMD=$(json_get "$INPUT" tool_input.command)

[[ -z "$CMD" ]] && exit 0

# Só age em git commit (momento em que os arquivos já estão staged) ou git add -f.
echo "$CMD" | grep -qE 'git +(commit|add)' || exit 0

PATTERN='(^|/)(\.venv|venv|__pycache__|node_modules)/|\.pyc$'
OFFENDERS=""

# 1) arquivos já staged que casam o padrão
STAGED=$(git diff --cached --name-only 2>/dev/null | grep -E "$PATTERN" || true)
# 2) git add -f <path> com padrão proibido explícito no comando
FORCED=""
if echo "$CMD" | grep -qE 'git +add .* -f|git +add +-f'; then
  FORCED=$(echo "$CMD" | tr ' ' '\n' | grep -E "$PATTERN" || true)
fi

OFFENDERS=$(printf '%s\n%s\n' "$STAGED" "$FORCED" | grep -E "$PATTERN" | sort -u || true)

if [[ -n "$OFFENDERS" ]]; then
  REASON="Bloqueado: artefato de build/ambiente prestes a ser commitado (.venv/__pycache__/*.pyc/node_modules). Remova do stage (git restore --staged) e garanta o .gitignore. Arquivos: $(echo "$OFFENDERS" | tr '\n' ' ')"
  echo "{\"decision\": \"block\", \"reason\": \"$REASON\"}"
  exit 2
fi

exit 0

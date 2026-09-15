#!/bin/bash
# .claude/hooks/post-write.sh
# PostToolUse — lint automático após Write/Edit
# Input: JSON via stdin com tool_input.file_path

INPUT=$(cat)
source "$(dirname "${BASH_SOURCE[0]}")/_json.sh"
FILE_PATH=$(json_get "$INPUT" tool_input.file_path)

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# Python: ruff (lint + format)
if [[ "$FILE_PATH" == *.py ]]; then
  if command -v ruff &>/dev/null; then
    ruff check --fix "$FILE_PATH" 2>/dev/null || true
    ruff format "$FILE_PATH" 2>/dev/null || true
  fi
fi

# TypeScript/TSX: prettier
if [[ "$FILE_PATH" == *.ts || "$FILE_PATH" == *.tsx ]]; then
  if command -v npx &>/dev/null; then
    npx prettier --write "$FILE_PATH" 2>/dev/null || true
  fi
fi

exit 0
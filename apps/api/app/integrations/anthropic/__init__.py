"""Integração com a API da Anthropic (Claude) para extração estruturada de
movimentações via tool use.

Princípios (CLAUDE.md §3 + Doc §12):
    - `ANTHROPIC_API_KEY` exclusivamente em variável de ambiente.
    - Arquivo é processado em memória (base64 ou texto), nunca em disco.
    - Logs nunca incluem o conteúdo do arquivo, prompt ou resposta da IA.
    - Cruzamento determinístico em código separado — IA só extrai, não decide
      match.

Estado atual (S9 — BACK 7.1):
    - `AnthropicClient.extract_movements(bytes, mime_type)` retorna
      `ExtractedStatement`.
"""

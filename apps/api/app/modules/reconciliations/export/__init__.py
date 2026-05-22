"""Exportação Excel do relatório de conciliação (S14 BACK 10.1).

Estrutura interna do módulo:
    - routes.py   — endpoint POST /api/v1/reconciliations/{session_id}/export
    - service.py  — orquestra crypto + cache L2 + workbook builder
    - workbook.py — builder openpyxl (5 abas)
    - styles.py   — paleta de cores, fontes, bordas reusáveis
    - schemas.py  — DTOs internos service ↔ workbook (NÃO expostos via API)
"""

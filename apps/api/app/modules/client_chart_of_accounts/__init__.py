"""Plano de contas do cliente (Sprint 10).

Traz para dentro do produto a classificação que a ORIGEM já tem — códigos,
hierarquia, situação, flags e o vínculo de cada categoria com a **conta de
demonstrativo**, que é o insumo do de-para da Sprint 12.

⚠️ **Não confundir com `client_categories`**, que é o catálogo de RÓTULOS de
clientes por organização ("Fintech", "Varejo"), governado por
`MANAGE_CLIENT_CATEGORIES` e pela tela "Categorias de Cliente". São coisas
diferentes e este módulo não reusa nem o repositório, nem o serviço, nem a
permissão daquele.

A BACK 10.1 entrega o modelo, o repositório e os schemas de domínio; o serviço
de sincronização é a 10.2 e as rotas são a 10.3.
"""

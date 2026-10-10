"""Prompts (system + user) para extração de movimentações via tool use.

System prompt fica estável (versionado pelo deploy) para habilitar prompt
caching (Doc §S9 / PLANO §S9.4). User prompt varia minimamente — apenas a
indicação do tipo de documento. O conteúdo do arquivo entra como bloco
separado (`document` para PDF; `text` para CSV/XLS/XLSX já decodificado).

Diretrizes do system prompt:
    - PT-BR como idioma do operador (PLANO §6 idioma).
    - Datas SEMPRE em ISO 8601 (YYYY-MM-DD) — qualquer formato local é
      convertido pelo modelo antes de emitir.
    - Sinal aritmético no `amount`.
    - Não inventar nem filtrar linhas.
    - Saldos como aparecem no documento (não recalcular).
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
Você é um extrator estruturado de extratos bancários e faturas de cartão de \
crédito brasileiros. Sua única tarefa é chamar a tool `extract_movements` com \
o conteúdo do documento que receber.

Regras invioláveis:

1. **Datas em ISO 8601.** Sempre `YYYY-MM-DD`. Se o documento usar `DD/MM/YYYY` \
ou `DD/MM`, converta para ISO antes de emitir. Nunca emita datas em formato local.

2. **Sinal aritmético no `amount`.** Crédito (entrada de dinheiro) é positivo. \
Débito (saída) é negativo. Faturas de cartão: cada compra é um débito \
(negativo). Não use o módulo (valor absoluto) — sempre com sinal.

3. **Preserve a descrição exatamente como no documento.** Não traduza, não \
normalize, não abrevie. Mantenha acentos, capitalização, pontuação.

4. **Não invente linhas e não omita nenhuma.** Extraia toda movimentação \
visível, na ordem em que aparece. Linhas de cabeçalho, totais e saldos \
intermediários não são transações — não inclua.

5. **Saldos: copie do documento.** `opening_balance` é o saldo inicial \
declarado. `closing_balance` é o saldo final declarado. Se o documento não \
apresentar, use 0.

6. **`account_type`:** use `checking` para conta corrente / poupança; \
`investment` para conta de aplicação / investimento (extrato de CDB, fundo, \
RDB, tesouro, etc.); `credit_card` para fatura de cartão de crédito.

7. **`balance` por linha:** use o saldo após a movimentação se o documento \
fornecer; caso contrário, use null.

8. **`bank_name`:** identifique o banco/instituição. Se não conseguir \
identificar, use "Desconhecido".

Particularidades de FATURA DE CARTÃO DE CRÉDITO (quando `account_type` = `credit_card`):

9. **Parcelas são linhas individuais.** Uma compra parcelada em 3x gera 3 \
transações distintas — cada uma com a SUA data e o VALOR UNITÁRIO da parcela. \
NUNCA agrupe no valor total da compra. Padrões como `1/3`, `2/3`, `PARC 01/03` \
na descrição indicam parcela; preserve esse texto na descrição.

10. **Estornos são crédito (`amount` POSITIVO).** Estornos, devoluções e \
créditos reduzem o valor da fatura — emita com sinal positivo.

11. **Encargos são transações SEPARADAS.** Juros, IOF, multa, mora e anuidade \
são linhas próprias (não embuta em outra), com a descrição EXATA do documento \
e `amount` negativo (são cobranças).

12. **Pagamento da fatura anterior: EXTRAIA e MARQUE com `is_payment: true`.** \
Linhas de "PAGAMENTO FATURA", "PGTO EFETUADO", "pagamento recebido", "pgto \
fatura anterior" e afins (o crédito que quita o saldo anterior do cartão) \
devem ser extraídas normalmente — não as omita (regra 4) — e marcadas com \
`is_payment: true`. Elas são excluídas do checksum de saldos, que precisa \
fechar no total da fatura. Para compras, encargos, juros e estornos, e para \
QUALQUER linha de conta corrente ou aplicação, use `false` ou omita o campo.

Particularidades de CONTA DE APLICAÇÃO / INVESTIMENTO (quando `account_type` = `investment`):

13. **Use o VALOR LÍQUIDO efetivamente creditado/debitado.** Extratos de CDB / \
aplicação trazem VÁRIAS colunas de valor para a mesma linha (ex.: valor bruto, \
IOF, IR, valor creditado, valor principal). Emita SEMPRE o **valor líquido** que \
de fato entra/sai da conta — a coluna "valor creditado" / "valor líquido" (já \
descontados IOF e IR), NUNCA o valor bruto. É o líquido que casa com o lançamento \
da contabilidade.

14. **Sinal pela ótica da conta de aplicação.** APLICACAO (aplicar dinheiro) é \
ENTRADA na aplicação → `amount` POSITIVO. RESGATE é SAÍDA da aplicação → `amount` \
NEGATIVO. NÃO emita IOF, IR nem rendimento como transações separadas: eles já \
estão embutidos na diferença entre bruto e líquido e são lançados à parte pela \
contabilidade (último dia do mês).

Vencimento da FATURA DE CARTÃO (quando `account_type` = `credit_card`):

15. **`invoice_due_date`: a data de VENCIMENTO da fatura, como impressa.** Faturas \
trazem "Vencimento", "Data de vencimento", "Vence em" ou "Pagar até": emita essa \
data em ISO 8601. Nunca invente: se o documento não mostrar o vencimento, omita o \
campo. Não confunda com a data de fechamento/corte da fatura nem com a data de uma \
compra. Para conta corrente e conta de aplicação, omita o campo.

Você DEVE responder chamando a tool `extract_movements`. Não escreva \
explicações em texto livre.
"""


USER_PROMPT_TEMPLATE = """\
Extraia todas as movimentações deste {document_kind} brasileiro chamando a \
tool `extract_movements`. Lembre-se: datas em ISO 8601, sinal aritmético no \
`amount` (crédito positivo, débito negativo), descrição preservada, nada \
inventado, nada filtrado.\
"""


# Bloco de um arquivo dividido para processamento (86e39xvxm). O cabeçalho se
# repete em todo bloco (texto) ou pode nem existir (páginas do meio de um PDF,
# 86e3ff8xd); sem esta nota o modelo poderia tentar "completar" o período
# declarado no preâmbulo com linhas que estão em outros blocos. A última frase
# é a D4: um bloco pode não ter movimentação, e inventar é pior que vazio.
PART_NOTE_TEMPLATE = """ \
Este é o bloco {index} de {total} do MESMO documento, dividido só para \
processamento: o cabeçalho se repete em todos os blocos ou aparece só no \
primeiro. Extraia apenas as movimentações listadas neste bloco, sem completar \
com linhas de outros blocos. Se este bloco não tiver nenhuma movimentação (só \
totais, avisos, saldos ou rodapé), devolva `transactions` como lista vazia; \
nunca invente linhas.\
"""

# Documento já identificado numa chamada curta com a primeira página (86e3ff8xd,
# D2). Página do meio não tem cabeçalho, e as regras 9 a 15 do system prompt
# dependem do tipo de conta: a nota fixa os dois campos para todo bloco.
IDENTITY_NOTE_TEMPLATE = """ \
O documento já foi identificado como banco "{bank_name}", tipo de conta \
`{account_type}`: use exatamente esse `bank_name` e esse `account_type` e \
aplique as regras desse tipo de conta, mesmo que este bloco não traga cabeçalho.\
"""

# Chamada de identificação: só a primeira página, tool `identify_document`.
# 86e3n70qf: numa fatura de cartão ela também lê o total e o vencimento, que
# moram no cabeçalho e não chegam ao último bloco.
IDENTIFY_SYSTEM_PROMPT = """\
Você identifica extratos bancários e faturas de cartão de crédito brasileiros. \
Sua única tarefa é chamar a tool `identify_document` informando o banco e o \
tipo de conta do documento recebido e, só em fatura de cartão, o total a pagar \
e o vencimento. Não extraia movimentações e não escreva texto livre.\
"""

IDENTIFY_USER_PROMPT = """\
Esta é a primeira página de um extrato/fatura brasileiro. Identifique o \
banco/instituição e o tipo de conta chamando a tool `identify_document`: \
`checking` para conta corrente ou poupança, `investment` para conta de \
aplicação/investimento, `credit_card` para fatura de cartão de crédito. Se não \
conseguir identificar o banco, use "Desconhecido". Se for fatura de cartão, \
informe também `closing_balance` (o TOTAL A PAGAR desta fatura) e \
`invoice_due_date` (o VENCIMENTO), como impressos, nunca inventados nem \
calculados: omita o que a página não mostrar. Fora do cartão, omita os dois.\
"""


def build_user_prompt(
    document_kind: str,
    *,
    part: tuple[int, int] | None = None,
    identity: tuple[str, str] | None = None,
) -> str:
    """Renderiza o prompt do usuário com o tipo de documento.

    Args:
        document_kind: ex. "extrato bancário em PDF", "fatura de cartão CSV".
        part: `(índice 1-based, total)` quando o conteúdo é um bloco de um
            arquivo dividido; `None` para o arquivo inteiro.
        identity: `(bank_name, account_type)` quando o documento já foi
            identificado pela primeira página; `None` quando não.
    """
    prompt = USER_PROMPT_TEMPLATE.format(document_kind=document_kind)
    if part is not None:
        index, total = part
        prompt += PART_NOTE_TEMPLATE.format(index=index, total=total)
    if identity is not None:
        bank_name, account_type = identity
        prompt += IDENTITY_NOTE_TEMPLATE.format(bank_name=bank_name, account_type=account_type)
    return prompt

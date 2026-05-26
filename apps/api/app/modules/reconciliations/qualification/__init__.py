"""Qualificação semântica/histórica/outlier dos pares conciliados (S19 BACK 12.1).

A reconciliação base (S10) só valida valor + data (CLAUDE.md §5.1-§5.2).
Esta camada roda **após** o matching, em cima dos pares já conciliados, e
gera anomalias auditáveis:

    - Camada 1 (IA): coerência semântica entre descrição do extrato e
      fornecedor/categoria do Omie. Gera `qualificacao_suspeita` /
      `qualificacao_incoerente`.
    - Camada 2 (SQL determinístico): padrão histórico — fornecedor antes
      classificado como X agora veio como Y. Gera `padrao_quebrado`.
    - Camada 3 (SQL determinístico): outlier de valor — |amount| > avg + 3*sigma
      do histórico do mesmo fornecedor. Gera `valor_outlier`.

Falha de qualquer camada NÃO derruba o pipeline — o caller (`job.py`)
envolve a chamada em try/except. CLAUDE.md §3 / §6.
"""

from app.modules.reconciliations.qualification.service import qualify_session

__all__ = ["qualify_session"]

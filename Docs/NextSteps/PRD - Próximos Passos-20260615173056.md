# PRD - Próximos Passos

# PRD — Próximos Passos: Sistema de Auditoria de Lançamentos

**Data:** 15/06/2026 | **Versão:** 2.0 **PM:** Lucas Landim **Stakeholders:** Laio Brito (CEO), Galhardo (Operação BPO), Pedro Silva (Dev)

---

## CONTEXTO E MOTIVAÇÃO

O sistema foi apresentado ao time de BPO em 27/05/2026 e validado positivamente. A reunião de 15/06/2026 mapeou três urgências:
**1\. Estabilização** — O Redis foi identificado como overengineering para o volume atual. O Upstash atingiu o limite e travou o sistema. Há também dois bugs ativos impedindo o uso.
**2\. Conciliação de faturas de cartão** — Esta funcionalidade deveria ter entrado no MVP mas ficou de fora. É a maior dor operacional do time segundo Galhardo: _"O cartão de crédito para mim hoje é o maior gargalo"_. Todos os clientes têm demanda de cartão de crédito, e o lançamento manual de cada transação é o processo mais custoso do BPO.
**3\. Open Finance via Pluggy** — Visão de médio prazo que automatiza a coleta de extratos e faturas, eliminando o processo manual de download por conta bancária. Arthur Souza (Cubos) avaliando proposta de integração.

---

## FASE 0 — ESTABILIZAÇÃO

### Problema

O sistema está inoperante por dependência de serviço externo desnecessário (Redis/Upstash). Dois bugs adicionais foram identificados durante os testes internos.

### O que muda

**Remoção do Redis:** O processamento assíncrono das sessões de conciliação foi implementado com Redis + Arq como broker de filas. Para o volume atual e perfil de uso do ADL — sessões criadas manualmente por um time pequeno, uma por vez — essa arquitetura é overengineering. O `BackgroundTasks` nativo do FastAPI é suficiente, elimina uma dependência externa e simplifica o sistema sem perda de funcionalidade.
**Bug: timeout da Claude API** — O limite de 2 minutos está causando falhas em conciliações com volume maior de transações. Identificado: clientes com mais dados estouram o timeout enquanto sessões menores (como os testes de Galhardo) passam sem erro.
**Bug: autenticação** — Token JWT não está sendo persistido corretamente após o login. O usuário é deslogado ao navegar para a primeira tela protegida.

### Critério de sucesso

O sistema processa uma conciliação de ponta a ponta sem erros e sem dependência de serviço externo além do PostgreSQL e da Claude API.

---

## FASE 1 — CONCILIAÇÃO DE FATURAS DE CARTÃO

### Problema

O processo de cartão de crédito é diferente da conta corrente em um ponto fundamental: **não existe provisão de contas a pagar para gastos no cartão**. O colaborador usa o cartão livremente durante o mês. Ao fechar a fatura, o operador BPO precisa:

1. Obter o PDF ou XLS da fatura
2. Lançar cada transação individualmente no Omie
3. Conciliar esses lançamentos com a conta cartão no Omie
   Este processo é feito manualmente para todos os clientes, todos os meses. É repetitivo, volumoso e propenso a erro de classificação.

### O que muda

O fluxo de conciliação de fatura de cartão reutiliza o mesmo pipeline da conta corrente — upload → IA → cruzamento → revisão — com adaptações para as particularidades do cartão:
**Particularidades da fatura de cartão:**

- Transações são débitos na conta cartão (saldo negativo)
- Estornos aparecem como créditos (positivos)
- Parcelas devem ser extraídas individualmente por data real de cada parcela, não agrupadas
- O pagamento da fatura em si não é uma transação da fatura — aparece no extrato da conta corrente como `DEB.CTA.FATURA`
- Encargos (juros, IOF, multa) são transações separadas com descrição específica
  **Regra de data:** tolerância zero, igual à conta corrente. Se a data da fatura diverge da data no Omie, a linha recebe status `conciliado_data_divergente` e uma anomalia `wrong_date` é gerada automaticamente. O operador revisa e decide — o sistema não concilia silenciosamente itens com datas diferentes.
  **Remoção do campo de tolerância de data:** o campo estava no formulário de nova conciliação. A decisão pós-apresentação (Laio: _"extrato bancário é tolerância zero"_) consolida que não faz sentido expor essa configuração ao usuário. O comportamento correto é fixo no sistema.

### Experiência do usuário

Do ponto de vista do operador, o fluxo é idêntico ao da conta corrente. A diferença visível está no formulário (contas do tipo cartão identificadas com rótulo `Cartão`), no header da tela de revisão (tipo de conta exibido), e nos filtros da aba de movimentações (`Compras` e `Estornos` em vez de `Créditos` e `Débitos`).

### Critério de sucesso

Um operador do BPO consegue fazer a conciliação de uma fatura de cartão real de ponta a ponta, sem subir o extrato da conta corrente, e exportar o relatório em menos de 5 minutos.

---

## FASE 2 — LANÇAMENTO AUTOMÁTICO DE TRANSAÇÕES DE CARTÃO NO OMIE

### Problema

Após a conciliação de fatura de cartão (Fase 1), o operador ainda precisa lançar manualmente cada transação no Omie antes de poder conciliar. Para clientes com cartões corporativos de alto volume, isso representa dezenas de lançamentos mensais por conta — o processo mais lento e mais propenso a erro do BPO.
O ADL já faz o parsing da fatura via IA e conhece cada transação com data, descrição e valor. A camada de lançamento no Omie é o passo natural que fecha o ciclo.

### O que muda

Após o parsing da fatura e a confirmação da prévia pelo operador, o ADL oferece a opção de **lançar automaticamente as transações no Omie** antes de iniciar a conciliação. O operador escolhe: lançar e conciliar em sequência, ou apenas conciliar (para os casos em que os lançamentos já foram feitos manualmente).
O lançamento usa o endpoint de criação de contas a pagar do Omie. Cada transação da fatura vira um lançamento na conta cartão correspondente, com fornecedor, categoria e data preenchidos. A categoria é sugerida pelo ADL com base no glossário do cliente (Fase 3) e no histórico de lançamentos anteriores — o operador pode revisar e ajustar antes de confirmar.
**Fluxo:**

1. Operador faz upload da fatura e confirma a prévia do parsing
2. ADL pergunta: "Deseja lançar as transações no Omie antes de conciliar?"
3. Se sim: ADL propõe categorias para cada transação. Operador revisa, ajusta o que precisar e confirma
4. ADL cria os lançamentos no Omie via API
5. ADL executa a conciliação automaticamente com os lançamentos recém-criados
6. Operador acessa diretamente a tela de revisão com o resultado
   **Particularidades:**

- Transações sem fornecedor identificado ficam pendentes de preenchimento manual antes da confirmação
- Estornos são tratados como crédito — o operador decide se estorna um lançamento existente ou cria um novo
- O ADL nunca cria um lançamento Omie sem confirmação explícita do operador

### Dependência

Requer que a Fase 1 (conciliação de fatura de cartão) esteja estável e validada em produção com dados reais antes de iniciar.

### Critério de sucesso

Um operador lança e concilia uma fatura de cartão completa no Omie através do ADL sem nenhum acesso direto ao Omie durante o processo.

---

## FASE 3 — GLOSSÁRIO E CLASSIFICAÇÃO POR CLIENTE

### Problema

A análise de anomalias de classificação é genérica — o sistema não sabe o que é correto ou incorreto para cada cliente específico. Galhardo pediu: _"Um glossário dizendo qual é a função de cada uma das categorias. A gente poder imputar essa informação e aí quando a gente puxar os extratos, a gente conseguir ter um parâmetro de classificação mais assertivo."_
Hoje o sistema detecta anomalias com tipos globais. Falta o contexto do plano de contas específico de cada cliente para que a análise seja precisa e não gere falsos positivos.

### O que muda

**Plano de contas por cliente:** cada cliente terá um cadastro de categorias com descrição de uso, fornecedores típicos e restrições. Serve como glossário para o operador durante a revisão e como contexto injetado no prompt da IA na fase de análise de classificação.
**Regras de auditoria por cliente:** o sistema permitirá cadastrar regras específicas — por exemplo, _"este cliente nunca deve ter lançamento de IOF classificado como juros"_ ou _"CNPJ próprio como fornecedor jamais deve ser classificado como receita de clientes"_. Nesta fase, as regras são exibidas como referência durante a revisão. Em fase futura, serão aplicadas automaticamente pela IA.

### Critério de sucesso

O operador consegue cadastrar categorias com descrição para um cliente e, ao revisar uma conciliação, visualiza o glossário como referência no momento de analisar anomalias de classificação.

---

## FASE 4 — OPEN FINANCE VIA PLUGGY

### Problema

Laio: _"Imagine que isso mataria um processo de ir para cada banco, abrir o banco, puxar o OFX, vai dentro do sistema, importa o OFX e faz esse processo. Para cada cliente, 20 clientes vezes 5 contas bancárias de cada cliente, fazer isso todos os dias — é um trabalhinho exaustivo."_
O processo manual de coleta de extratos e faturas é o maior gargalo de escala do BPO. Com Open Finance, esse processo é automatizado.

### O que é o Pluggy

Pluggy é uma plataforma de Open Finance que oferece:
**Pluggy Connect** — widget drop-in que o ADL embute no próprio frontend. O usuário (operador ou cliente) se autentica com as credenciais do banco diretamente no widget, sem que o ADL toque nas credenciais bancárias. O widget cuida de MFA, validações de credencial e tratamento de erros por instituição. Funciona em web, iOS, Android, React Native, Flutter e Next.js.
**Item** — representação de uma conexão com uma instituição financeira. Quando um Item é criado e a sincronização termina, o Pluggy recupera os dados financeiros dos últimos 365 dias. Os **produtos** coletados relevantes para o ADL são:

- `Account` — dados da conta e saldo em tempo real
- `Transaction` — movimentações da conta corrente
- `Credit Card Bills` — faturas do cartão de crédito
- `Credit Card Installments` — detalhamento de parcelas
  **Consent** — criado na primeira conexão, com data de expiração regulada pelo Banco Central (Open Finance). Quando expira, o usuário precisa reconectar via widget. O ADL precisa monitorar e notificar antes da expiração.
  **Webhook** — o Pluggy notifica via webhook (`item/updated`) quando novos dados chegam. O ADL não precisa de polling — reage a eventos.

### Dois casos de uso distintos

**Caso A — Conta corrente (substituição do OFX manual):** Com o Item conectado, o ADL recebe webhook quando novas transações chegam e processa a conciliação automaticamente ou notifica o operador para revisar. Produto Pluggy: `Account` + `Transaction`.
**Caso B — Cartão de crédito (substituição do upload de fatura):** O processo é diferente — não há provisão de contas a pagar para gastos no cartão. Com Open Finance, o ADL coleta as transações do cartão diretamente, propõe o lançamento no Omie e a conciliação. O operador revisa e aprova. Produto Pluggy: `Credit Card Bills` + `Credit Card Installments`.

### Fluxo de integração com o Pluggy

```cs
1. CONEXÃO (feita uma vez por conta bancária por cliente)
   ADL backend gera connect_token via Pluggy API (server-side, usando API keys da Hologram)
   → Frontend abre Pluggy Connect widget com o connect_token
   → Usuário autentica com as credenciais bancárias dentro do widget
   → Pluggy cria o Item e retorna itemId
   → ADL armazena itemId vinculado ao client_id e omie_conta_id

2. SINCRONIZAÇÃO (automática, via webhook)
   Pluggy envia POST para endpoint do ADL quando item/updated
   → ADL busca transações do período via Pluggy API usando itemId
   → ADL processa conciliação automática ou notifica operador

3. GESTÃO DE CONSENT
   ADL monitora consent_expires_at de cada conexão
   → N dias antes da expiração: notifica operador via Slack
   → Operador reabre o widget para renovar a conexão
```

### O que muda na experiência do operador

A mudança é radical: ao invés de fazer upload de arquivo, o operador acessa a tela do cliente e encontra as conciliações já processadas (ou aguardando revisão). A tela de detalhe do cliente exibe o status de cada conexão Pluggy — ativa, expirada, com erro — ao lado do histórico de conciliações.
O fluxo de upload manual é mantido como fallback para bancos não cobertos pelo Pluggy ou situações pontuais.

### Schema de dados necessário

Nova tabela `client_pluggy_connections`:

| Campo                | Descrição                                                 |
| -------------------- | --------------------------------------------------------- |
| `client_id`          | FK → clients                                              |
| `omie_conta_id`      | Conta correspondente no Omie                              |
| `pluggy_item_id`     | ID do Item no Pluggy — referência para todas as consultas |
| `pluggy_account_id`  | ID da conta específica dentro do Item                     |
| `bank_name`          | Nome do banco (para exibição)                             |
| `account_type`       | `checking` ou `credit_card`                               |
| `consent_expires_at` | Data de expiração do consent — monitorada ativamente      |
| `last_sync_at`       | Última sincronização bem-sucedida                         |
| `status`             | `active`, `expired`, `error`                              |

### Dependências e riscos

**Dependência externa:** proposta de Arthur Souza (Cubos) aguardada para 16/06/2026. Se o custo for inviável, a integração é feita internamente — o Pluggy tem SDK Python e documentação estruturada.
**Cobertura de bancos:** verificar se Sicredi, BNB e Cora estão cobertos pelos conectores do Pluggy. Bancos menores podem não ter conector, exigindo manter o upload manual como fallback permanente.
**Onboarding de clientes:** cada cliente precisa autorizar individualmente a conexão via widget. O processo de onboarding de novos clientes no ADL passa a incluir essa etapa. Para clientes existentes, será necessário um processo de migração.
**Expiração de consent:** regulada pelo Banco Central, não pelo Pluggy. Precisa ser gerenciada ativamente para não haver interrupção na coleta automática.

### Critério de sucesso

A Hologram conecta ao menos 3 bancos de clientes reais via Pluggy e processa a conciliação mensal de um cliente completo (todas as contas) sem nenhum upload manual de arquivo.

---

## ROADMAP

```yaml
FASE 0 — Estabilização (urgente, pré-requisito para tudo)
  Remoção do Redis → BackgroundTasks nativo
  Bug fix: timeout Claude API
  Bug fix: autenticação JWT

FASE 1 — Conciliação de Faturas de Cartão (alta prioridade)
  Suporte a contas de cartão no fluxo existente
  Parsing de fatura via IA com particularidades de cartão
  Cruzamento com regras específicas para cartão
  Tolerância zero para datas + anomalia wrong_date

FASE 2 — Lançamento Automático de Transações de Cartão no Omie
  Sugestão de categoria por transação (com base no glossário do cliente)
  Criação de lançamentos no Omie via API com confirmação do operador
  Fluxo integrado: lançar → conciliar em sequência na mesma sessão

FASE 3 — Glossário e Classificação por Cliente (média prioridade)
  Plano de contas por cliente
  Regras de auditoria específicas por cliente
  Preparação para detecção automática de anomalias via IA

FASE 4 — Open Finance via Pluggy (médio prazo)
  Decisão: integração interna vs parceria Arthur Souza
  Verificação de cobertura dos bancos da carteira atual
  Pluggy Connect widget embutido no ADL
  Coleta automática via webhook
  Gestão de consents e notificação de expiração

FASE 5 — Rotinas Automáticas de Auditoria (longo prazo)
  Auditoria diária: verificação de lançamentos e contas sem movimentação
  Auditoria semanal: comportamento da semana e comparação com previsto
  Auditoria mensal: análise horizontal entre períodos
  Alertas via Slack e lembretes de transações recorrentes
```

---

## FASE 5 — ROTINAS AUTOMÁTICAS DE AUDITORIA

### Problema

Hoje a auditoria é iniciada manualmente pelo operador. Galhardo mapeou três frequências necessárias de auditoria que precisam ocorrer de forma contínua, independente de upload de extrato:

- **Diária:** verificar se as contas estão atualizadas no Omie, detectar contas sem lançamentos em dias em que normalmente há movimentação, identificar possíveis transações duplicadas no mesmo dia
- **Semanal:** analisar o comportamento da semana anterior — o que estava previsto vs o que foi realizado, o que ficou pendente
- **Mensal:** análise horizontal entre meses — mesmas categorias e fornecedores comparados com períodos anteriores para detectar anomalias de classificação e variações anormais de valor
  Galhardo também mencionou a necessidade de **lembretes de transações recorrentes**: pagamentos que acontecem todo mês em datas fixas e que o time não pode esquecer de lançar.

### O que muda

O ADL passa a executar rotinas automáticas disparadas pelo Cloud Scheduler (GCP), sem depender de ação humana. Os resultados chegam ao operador via Slack ou são acessíveis na tela do cliente como um painel de saúde diária.
**Rotina diária:**

- Para cada cliente ativo, verifica no Omie se há lançamentos nas contas dos últimos 2 dias
- Contas que normalmente têm movimentação diária e ficaram sem lançamentos geram alerta `account_inactive`
- Lançamentos com mesmo valor, mesmo fornecedor e mesma data em uma única conta geram alerta de possível duplicata
  **Rotina semanal:**
- Compara lançamentos previstos (contas a pagar criadas) com os efetivamente baixados na semana
- Lista o que ficou em aberto e os vencimentos da semana seguinte
- Envia resumo consolidado por cliente no Slack do time BPO
  **Rotina mensal:**
- Análise horizontal: compara categorias e valores do mês atual com a média dos 3 meses anteriores
- Variações acima de um limiar configurável geram anomalia para revisão
- Detecta fornecedores que mudaram de categoria entre meses
  **Lembretes de recorrentes:**
- Cada cliente pode ter um cadastro de pagamentos recorrentes (fornecedor, valor aproximado, dia do mês)
- N dias antes da data, o ADL notifica o operador responsável via Slack

### Dependências

- **Fase 3 (Glossário):** necessário para que a análise horizontal tenha contexto de classificação correta por cliente
- **Fase 4 (Pluggy):** idealmente conectado — as rotinas diárias são muito mais poderosas quando os dados bancários chegam automaticamente. Sem Pluggy, as rotinas diárias analisam apenas o Omie, sem comparação com o banco
- **Cloud Scheduler:** disparador das rotinas sem Redis, sem fila de mensagens

### Critério de sucesso

O time BPO recebe notificações automáticas no Slack sobre contas com anomalia antes de precisar verificar manualmente. A rotina mensal identifica pelo menos uma inconsistência de classificação horizontal por cliente em cada ciclo.

---

## FORA DE ESCOPO NESTE PRD

- Integração com Open Finance para contas a pagar — o processo de provisão já existe no Omie e não é dor mapeada
- Relatórios automatizados para clientes finais — passo posterior à estabilização das rotinas internas

---

_Sistema de Auditoria de Lançamentos (ADL) — Hologram GestãoPRD v3.0 — 15/06/2026Referência Pluggy:_ [_https://docs.pluggy.ai_](https://docs.pluggy.ai)

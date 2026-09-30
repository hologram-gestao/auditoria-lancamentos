# Copy da landing pública (subtask 86e3fr9uf)

> **Para quem é este arquivo:** o Lucas, que revisa o texto antes de a página ir ao ar, e quem
> for editar a landing depois. O texto que a página mostra mora em
> `apps/web/src/components/landing/content.ts`; este arquivo é a versão comentada, com a fonte
> de cada dor e a evidência de cada afirmação. **Mudou aqui, muda lá** (e vice-versa).
>
> Escrito em 29/09/2026 a partir de `Docs/reunioes/` (reuniões com o Murilo, 01 e 02/09),
> do PRD em `Docs/NextSteps/` e do `CLAUDE.md` (§1, §3.15, §4, §5, §8). As transcrições de
> 04/09, 17/09 e 28/09 não estão no repositório e **não** foram usadas.

---

## Regras que o texto segue

- Português do Brasil, frases curtas, segunda pessoa ("seu cliente", "seu fechamento").
- Nenhum número de resultado (horas economizadas, % de acerto, quantidade de clientes): não
  existe medição. Os únicos números da página são regras do produto (um centavo, três dias).
- O produto se chama **Hologram OS** (decisão do Pedro, 30/09/2026) e a página o nomeia: no
  header, no hero, na resposta às dores, em "Como funciona" e no rodapé, sem repetir em todo
  bullet. Quem trata o dado do formulário é a empresa, **Hologram Gestão**. Em `content.ts` o
  nome vem sempre de `PRODUCT_NAME` (`lib/brand.ts`); o nome antigo e a sigla antiga não
  aparecem (um teste do front trava as duas coisas).
- Sem superlativo, sem "inteligente" como adjetivo, sem "garantimos", sem travessão, sem emoji,
  sem jargão de desenvolvimento (tenant, endpoint, chave de dados).
- Segurança como ela é: o arquivo passa por um provedor de IA para a leitura, e a qualificação
  também manda ao provedor as descrições das movimentações e o glossário do cliente
  (86e3anx75). Por isso a página não diz "apenas para a leitura". Ela **não** afirma nada
  sobre retenção ou treinamento do provedor (task 86e3anx75 aberta).

---

## (a) Dores e como a plataforma responde

Ordem: da mais cara para a mais barata, pela régua das reuniões com o Murilo.

| #   | Dor (como o público sente)                                                                                    | Fonte                                                                    | O que existe na `main` e sustenta a resposta                                                                                                                                                                                                         |
| --- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | O fechamento não roda todo mês; vira um mutirão no começo do ano                                              | Resumo geral, "Dores consolidadas" nº 1; R2 "O contábil é o gargalo"     | Conciliação por conta e mês (`uq_recon_sessions_account_month`, Sprint 4), lista de conciliações, revisão por abas, relatório Excel (S11 a S14)                                                                                                      |
| 2   | Dado ruim entra na contabilidade: imobilizado como despesa, ajuste de saldo inventado, tarifas somadas errado | R1 "As dores" nº 2 e o caso do imobilizado                               | Cruzamento determinístico com tolerância de R$ 0,01 e 3 dias (`CLAUDE.md` §5), anomalias tipadas (S15), qualificação por IA que sinaliza e não decide (S19, §5.6)                                                                                    |
| 3   | Categorizar, fazer de-para e montar lançamento é PROCV manual, cliente a cliente                              | Dores consolidadas nº 2 e 3; R2 "Eu queria ter 50 clientes que nem esse" | De-para por cliente e destino (Sprint 12), plano contábil do cliente e partida pelo sinal (Sprint 16), arquivo no layout do sistema contábil (Sprint 13, na `main`; importação real no Domínio ainda não validada), glossário por cliente (Sprint 6) |
| 4   | A maioria dos clientes não tem ERP; chega planilha e extrato em todo formato                                  | R2 "Só 6–7 usam Omie"; Dores consolidadas nº 4                           | Conexão com Omie (Sprint 9), origem por arquivo com mapeamento de entrada (Sprint 14), leitura de extrato e fatura por IA em PDF, CSV, XLSX e XLS (`parse_service.py`, S9, Sprint 2), validação humana de amostra (§1 passo 3)                       |
| 5   | Fatura de cartão é trabalho à parte e ninguém lança                                                           | Operação da Hologram                                                     | Conciliação de fatura (Sprint 1) e lançamento das compras no Omie (Sprint 7; `OMIE_POSTING_ENABLED` ligado em dev desde 21/08)                                                                                                                       |
| 6   | Título vencido há meses não aparece na conciliação do mês                                                     | Operação da Hologram                                                     | Carteira de títulos sem recorte de mês (Sprint 11) e contexto do título (Sprint 15)                                                                                                                                                                  |
| 7   | O escritório não pode vazar um cliente para o outro; o cliente final quer ver o próprio dado                  | Camada de organizações e Sprint 5                                        | Acesso do cliente ao próprio cadastro, organização só alcança os próprios clientes (§3.15)                                                                                                                                                           |
| 8   | "Onde meus dados ficam e quem lê?"                                                                            | 86e3anx75                                                                | Chave própria por cliente (§4.1), arquivo original nunca guardado (§4.6), trilha de acesso (§4.7), destruição da chave no encerramento (§4.12)                                                                                                       |

A página usa as dores 1 a 4 no bloco "Dores e respostas"; a 5 e a 6 entram nos cards de
público; a 7 e a 8, no bloco de segurança.

---

## (b) Texto final, bloco a bloco

### 1. Header

- Marca: logomark + "Hologram OS" (abaixo de 640px, só a logomark).
- Botão secundário: **Entrar** (vai para `/login`).
- Botão primário: **Entrar em contato** (rola até o formulário).

### 2. Hero

- Sobretítulo (em pílula, com um ponto de luz à esquerda): **Hologram OS, para escritórios de
  contabilidade, BPOs financeiros e empresas**
- Título: **O financeiro do seu cliente, _conferido_ antes de virar contabilidade.** A palavra
  "conferido" é o único destaque do hero (gradiente da marca).
- Subtítulo: O Hologram OS cruza extrato, fatura e planilha com o que foi lançado, mostra o que
  não bate e deixa pronto o que segue para o sistema contábil. Sua equipe revisa as exceções,
  não o mês inteiro.
- Botões: **Entrar em contato** (primário) e **Entrar** (secundário).
- Chips abaixo dos botões: **Lê PDF, XLSX, XLS, CSV e Omie** · **Entrega Excel, Omie e o
  arquivo contábil**.
- Vinheta ao lado: card "Conciliação · Conta corrente · Março" com cinco linhas fictícias de
  "Cliente exemplo" cujos selos passam de "Pendente" para "Conciliado", e um rodapé "Diferença
  de saldo R$ 0,00". Sobre o canto inferior esquerdo, um mini-card flutuante com o selo
  **Classificação suspeita** e a linha **IOF lançado como juros** (a anomalia que a revisão
  aponta).

### 3. Para quem

Rótulo: **Para quem** · Título do bloco: **Feito para quem fecha o financeiro de outras empresas**

| Card | Título                      | Texto                                                                                                                                 |
| ---- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Escritório de contabilidade | Saiba se o cliente está pronto para a integração contábil antes de importar. O que não bate aparece na conferência, não no balancete. |
| 2    | BPO financeiro              | Concilie cada conta, mês a mês, com revisão por etapas, relatório em Excel e as compras do cartão lançadas no Omie.                   |
| 3    | Empresa                     | Com ERP ou só com planilha, seu financeiro chega conferido ao contador, com os títulos vencidos e as exceções explicados.             |

### 4. Dores e respostas

Rótulo: **Dores e respostas** · Título do bloco: **O trabalho que ninguém vê, e que decide o
fechamento** · Rótulos de cada par: **O problema** e **Como o Hologram OS responde**

| Par | Dor                                                                                                                                                                 | Resposta                                                                                                                                                                                                                                                                       |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | O fechamento não roda todo mês. Fica para depois, e o erro de janeiro só aparece quando o balanço aperta.                                                           | Cada conta de cada mês vira uma conciliação própria, com o que já foi conferido e o que falta. Dá para fechar mês a mês sem montar planilha.                                                                                                                                   |
| 2   | Dado ruim entra na contabilidade: imobilizado lançado como despesa, ajuste de saldo inventado, tarifas somadas errado. Corrigir depois custa mais que lançar certo. | Cada movimentação é comparada com o que foi lançado, por valor e data, com regra fixa. O que não bate vira anomalia com tipo e motivo. A IA aponta o que parece incoerente; quem decide é a sua equipe.                                                                        |
| 3   | Categorizar, fazer o de-para e montar o lançamento contábil vira PROCV, cliente a cliente.                                                                          | Cada cliente tem o próprio de-para da categoria para a conta contábil, com o plano de contas dele e o histórico padrão. Débito e crédito saem do sinal da movimentação, e o arquivo sai no layout que o sistema contábil importa, hoje em validação com escritórios parceiros. |
| 4   | A maioria dos clientes não tem ERP. Chega planilha e extrato em todo formato, pelo WhatsApp.                                                                        | Cliente com Omie é conectado direto. Cliente sem sistema manda a planilha do mês, lida por um mapeamento configurado uma vez. Extrato e fatura em PDF ou planilha são lidos por IA, e você confere uma amostra antes de seguir.                                                |

### 5. Como funciona

Rótulo: **Como funciona** · Título do bloco: **Do arquivo ao lançamento, em quatro passos**

1. **Envie o arquivo ou conecte a origem.** Extrato, fatura de cartão ou planilha do cliente.
   Se ele usa Omie, o Hologram OS busca os lançamentos por conta própria.
2. **O Hologram OS lê e cruza.** A IA extrai as movimentações do arquivo. O cruzamento com os
   lançamentos segue regra fixa: até um centavo de diferença no valor e até três dias na data.
3. **Sua equipe revisa o que ficou de fora.** Divergências, lançamentos sem par e anomalias
   aparecem separados, com espaço para a nota de resolução de cada um.
4. **Sai o relatório e o lançamento.** Relatório da conciliação em Excel, compras do cartão
   lançadas no Omie e o arquivo contábil no layout do seu sistema.

### 6. Segurança e privacidade

Rótulo: **Segurança e privacidade** · Título do bloco: **O dado do seu cliente continua dele**

| Item | Título                               | Texto                                                                                                                                                                                                     |
| ---- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Uma chave por cliente                | Os dados sensíveis de cada cliente são cifrados com uma chave própria. O conteúdo de um cliente não abre com a chave de outro.                                                                            |
| 2    | O arquivo original não fica guardado | O arquivo é lido por IA para extrair as movimentações e não fica armazenado pela plataforma. Na revisão, a IA também apoia sua equipe apontando o que parece incoerente; quem decide é sempre uma pessoa. |
| 3    | Cada um vê só o que é seu            | O escritório alcança só os próprios clientes, e o cliente final só a própria empresa. Exportações e tentativas de acesso negadas ficam registradas.                                                       |
| 4    | Encerrou, acabou                     | Quando um cliente sai, a chave dele é destruída e o conteúdo cifrado deixa de poder ser lido.                                                                                                             |

Link ao final: **Leia o aviso de privacidade** (`/privacidade`). Ao lado, botão secundário
**Baixar o manual (PDF)** com "PDF, 3 MB" (`/manual-hologram-os.pdf`, cópia de
`Docs/manual/Manual-Hologram-OS.pdf`); o mesmo link aparece na confirmação do formulário
como **Enquanto respondemos, leia o manual**.

### 7. Formulário de contato (`#contato`)

- Rótulo: **Contato** · Título: **Fale com a gente**
- Linha de apoio: Conte um pouco do seu cenário. Respondemos pelo e-mail informado.
- Campos: **Nome** · **E-mail** · **Empresa ou escritório** (opcional) · **WhatsApp**
  (opcional) · **Mensagem** (opcional)
- Consentimento (obrigatório): ver (d).
- Botão: **Enviar** (enquanto envia: "Enviando…")
- Sucesso: **Recebemos sua mensagem.** Vamos responder pelo e-mail informado.
- Erro de limite (429): Muitas mensagens em pouco tempo. Tente de novo em um minuto.
- Erro genérico: Não foi possível enviar agora. Tente de novo em instantes.
- Erros de campo: "Informe seu nome." · "Informe um e-mail válido." · "Use até N caracteres." ·
  "Use só números, espaço, +, parênteses e hífen." · "Marque a autorização para enviarmos."

### 8. Rodapé

"© {ano} Hologram Gestão" · "Hologram OS" ao lado · link **Entrar** · link **Aviso de
privacidade**. O ano é o do build.

---

## (c) Afirmações e evidência

| Afirmação na página                                                                      | Evidência                                                                                                                                                    |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Chips: lê PDF, XLSX, XLS, CSV e Omie; entrega Excel, Omie e o arquivo contábil           | `_ALLOWED_EXTENSIONS` em `parse_service.py`, conexão Omie (Sprint 9), export Excel (S14), `IncluirLancCC` (Sprint 7), `modules/accounting_files` (Sprint 13) |
| Cruza extrato, fatura e planilha com o que foi lançado                                   | Fluxo núcleo (`CLAUDE.md` §1), matcher (`processing/matcher.py`), fatura de cartão (Sprint 1), planilha por origem de arquivo (Sprint 14)                    |
| Cada conta de cada mês vira uma conciliação própria                                      | `UNIQUE(client_id, omie_conta_id, reference_month)`, §4.10                                                                                                   |
| Regra fixa: até um centavo e até três dias                                               | `AMOUNT_TOLERANCE = 0.01`, `DATE_DIVERGENCE_RANGE = 3`, §5.1 e §5.2                                                                                          |
| O que não bate vira anomalia com tipo e motivo                                           | `anomaly_types` (S15), anomalias na revisão                                                                                                                  |
| A IA aponta; quem decide é a equipe                                                      | §5.6 ("IA nunca decide match"), qualificação S19 com veredito do revisor (Sprint 6)                                                                          |
| De-para por cliente, plano de contas, histórico padrão                                   | Sprint 12 e Sprint 16 (`client_mapping_decisions.history_encrypted`)                                                                                         |
| Débito e crédito saem do sinal                                                           | `partida.derive_partida` (Sprint 16)                                                                                                                         |
| Arquivo no layout que o sistema contábil importa, em validação com escritórios parceiros | Sprint 13 na `main` (`modules/accounting_files`, `modules/export_layouts`); importação real no Domínio pendente (§8, parágrafo da Sprint 13)                 |
| Cliente com Omie conectado direto                                                        | `client_connections` (Sprint 9)                                                                                                                              |
| Planilha lida por um mapeamento configurado uma vez                                      | `client_input_mappings` (Sprint 14)                                                                                                                          |
| Extrato e fatura em PDF ou planilha lidos por IA                                         | `_ALLOWED_EXTENSIONS = {".pdf", ".csv", ".xlsx", ".xls"}` em `reconciliations/parse_service.py`                                                              |
| Você confere uma amostra antes de seguir                                                 | §1 passo 3 ("Humano valida amostra")                                                                                                                         |
| Compras do cartão lançadas no Omie                                                       | Sprint 7, `IncluirLancCC` verificado em 21/08; `OMIE_POSTING_ENABLED` ligado em dev                                                                          |
| Títulos vencidos explicados                                                              | `client_titles` (Sprint 11), `title_contexts` (Sprint 15)                                                                                                    |
| Uma chave por cliente; não abre com a de outro                                           | DEK por cliente + AAD, §4.1                                                                                                                                  |
| O arquivo é lido por IA e não fica armazenado                                            | Extração em `integrations/anthropic/client.py`; arquivo nunca persistido, §3.10 e §4.6                                                                       |
| Na revisão, a IA aponta o que parece incoerente; quem decide é uma pessoa                | Qualificação S19 (`reconciliations/qualification/semantic.py`, que envia descrições e glossário ao provedor), veredito do revisor (Sprint 6), §5.6           |
| Escritório só os próprios clientes; cliente final só a própria empresa                   | `resolve_client_access`, §3.15 e §4.8                                                                                                                        |
| Exportações e acessos negados registrados                                                | `access_audit` com `export` e `denied` (§4.7; `core/audit.py`, export da conciliação, arquivo contábil)                                                      |
| Chave destruída quando o cliente sai                                                     | `dek_wrapped → NULL` no encerramento, §4.12                                                                                                                  |

---

## (d) Consentimento e aviso de privacidade

**Versão do texto:** `2026-09-30` (constante `CONSENT_TEXT_VERSION` no backend e no front;
mudar o texto = mudar a versão nos dois).

**Texto do consentimento, ao lado do checkbox:**

> Autorizo a Hologram a usar estes dados para entrar em contato comigo sobre a plataforma,
> conforme o aviso de privacidade.

**Página `/privacidade` (Aviso de privacidade do formulário de contato):**

> **Aviso de privacidade**
>
> Este aviso vale para o formulário de contato do Hologram OS. Ele não trata dos dados que os
> clientes da plataforma processam nela.
>
> Versão de 30 de setembro de 2026.
>
> **Quais dados coletamos.** Nome e e-mail, obrigatórios. Empresa ou escritório, WhatsApp e
> mensagem, se você preencher. Guardamos também a data do seu consentimento e a versão do texto
> que você aceitou.
>
> **O que não coletamos.** O contato é gravado sem o seu endereço IP e sem dados do seu
> navegador, e o formulário não usa cookies de rastreamento. Os registros técnicos de acesso do
> servidor, comuns a qualquer site, ficam por tempo limitado e não são ligados ao seu contato.
>
> **Para quê.** Só para responder ao seu contato e conversar sobre a plataforma. Não vendemos,
> não emprestamos e não usamos esses dados para outra finalidade.
>
> **Base legal.** O seu consentimento, dado ao marcar a autorização antes de enviar (Lei Geral
> de Proteção de Dados, art. 7º, I).
>
> **Quem lê.** A equipe da Hologram Gestão. O aviso de cada contato chega a um canal interno
> da equipe.
>
> **Por quanto tempo.** Até você pedir a exclusão, ou por até 12 meses sem nova conversa, o que
> vier primeiro.
>
> **Seus direitos.** Você pode pedir acesso, correção ou exclusão dos seus dados, e retirar o
> consentimento, a qualquer momento. Envie o pedido pelo mesmo formulário, informando o e-mail
> usado no contato.

⚠️ **A confirmar com o Pedro:** um e-mail da Hologram para pedidos de privacidade. Quando
existir, ele substitui "pelo mesmo formulário" aqui e em `content.ts`.

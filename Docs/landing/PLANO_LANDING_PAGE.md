# Plano de execução: landing page pública e captação de leads

> **Épico ClickUp:** `86e3fr9tj` ([ÉPICO] Landing page pública e captação de leads), 5 subtasks
> (`86e3fr9uf` copy, `86e3fr9ut` back, `86e3fr9vz` front, `86e3fr9wm` infra, `86e3fr9x3` rename).
> **Prazo:** 1 a 3 até o evento de contadores de 30/09/2026; 4 e 5 dependem do nome do produto.
> **Planejado em** 29/09/2026 (sessão de planejamento, Fable). **Executado por** um agente
> construtor (Opus), em sessão separada, sem parar até a landing estar pronta. **Revisado** de
> volta na sessão de planejamento antes do push.
>
> Este documento é a fonte da verdade da execução. Toda decisão abaixo já está TOMADA para o
> executor não parar; o que for descoberto como errado se corrige em edição, depois, não em
> pausa. As regras do repositório (`CLAUDE.md`, skills `migration`, `endpoint-tenant`,
> `front-gate`, `gate`, `entrega`) continuam valendo por cima deste plano.

---

## 1. Objetivo e público

**O que é:** a raiz do sistema (`/`) deixa de redirecionar para o login e vira uma página
pública que apresenta a plataforma, com dois caminhos: **Entrar** (vai para `/login`) e **Entrar
em contato** (rola até o formulário da própria página). O formulário grava o lead no banco e
avisa o canal da ADL no Slack. Usuário logado que abre `/` vai para `/clientes`.

**Para quem a página fala** (nesta ordem, porque o evento de 30/09 é de contadores):

1. **Escritórios de contabilidade e contadores parceiros**, que recebem o financeiro de dezenas
   de clientes em qualidade ruim e pagam o preço no fechamento. Frame decidido nas reuniões com
   o Murilo (`Docs/reunioes/`): a plataforma responde à pergunta *"esse cliente está pronto para
   a minha integração contábil?"*.
2. **BPOs financeiros e gestores financeiros**, que conciliam, revisam e lançam todo mês
   (o uso original da Hologram).
3. **Empresas** que têm ERP ou só planilha e querem o próprio financeiro auditado antes de ir
   para o contador.

**O que a página NÃO é:** não é catálogo de features, não é documentação, não tem preço, não
promete número nenhum que o produto não meça hoje, e não usa o nome do produto (ele ainda não
existe; ver §9).

---

## 2. Decisões tomadas (o executor não pergunta, executa)

| # | Decisão | Motivo |
|---|---|---|
| D1 | **Tema Hologram fixo** na landing e em `/privacidade`; sem seletor de tema | Página de marca; o token da marca é `.hologram` em `globals.css:125`. Fixar também tira a landing da variação por tema (o gate continua rodando nos 3 temas, com resultado idêntico) |
| D2 | **Sem nome de produto.** A copy fala "a plataforma da Hologram" e "Hologram". Nasce **uma constante** de marca (`apps/web/src/lib/brand.ts` e `apps/api/app/core/branding.py`) usada em título, metadados, login, header e landing | A subtask 4 está bloqueada no nome; centralizar agora faz a subtask 5 virar mudança de uma linha |
| D3 | **Sem foto de banco de imagens na v1.** A riqueza visual vem de CSS e SVG: fundo com gradientes da marca em movimento lento, vinhetas de produto **animadas** construídas com os próprios primitivos da UI, cards com vidro e brilho no hover (ver §4). Ficam **dois slots** de `next/image` (16:9 e 4:3) com placeholder documentado, para as imagens do Magnific que o Pedro baixar e conferir a licença | O executor não pode baixar imagem nem conferir licença; CSP tem `img-src 'self'`, então qualquer imagem tem de ser servida de `apps/web/public/landing/`. Uma landing moderna não depende de foto: depende de composição, tipografia e movimento |
| D4 | **Webfont só se o arquivo já estiver no repo.** Se existir `apps/web/src/fonts/*.woff2` (o Pedro pode baixar Manrope ou Inter do Google Fonts e colocar lá), a landing usa `next/font/local` escopado no layout público; se não existir, usa a pilha `font-sans` do app, com escala tipográfica generosa (título 3xl a 6xl, `tracking-tight`, `leading-[1.05]`) | `next/font/google` baixa no build e o sandbox do executor não tem rede. A fonte não pode bloquear a entrega, mas a tipografia é metade da "cara moderna" |
| D12 | **A landing tem de ser bonita e moderna, com efeitos visuais, nas cores da Hologram.** Pedido explícito do Pedro (29/09). Isso não estava nas subtasks: a 1 só cita as cores, a 3 não fala de estética. A §4 descreve o que "moderna" significa aqui, e o critério de aceite visual é do Pedro e do Lucas, pelo print e pelo link em dev | Landing é peça de marca no evento de 30/09; uma página correta e sem graça falha no objetivo mesmo passando em todo gate |
| D5 | **Anti-spam em 4 camadas, sem CAPTCHA:** honeypot, tamanho estrito por campo, limite por e-mail (3 em 24h, silencioso), e `slowapi` como teto global | CAPTCHA traz script de terceiro (CSP), chave e dependência externa. O IP visto pela API é o do proxy (`86e3anx10`), então o `slowapi` por IP é um teto **global por instância**: é rede de segurança, não a defesa principal. Dívida registrada, ver §5.4 |
| D6 | **Aviso do Slack inline, fail-soft, timeout 3 s**; `notified_at` gravado na mesma transação quando entrega | Simples, testável, sem sessão de banco em background; o visitante espera no máximo 3 s a mais |
| D7 | **Lead em claro**, só os campos decididos, sem IP, sem user agent | Decisão do Pedro (28/09): dado de prospect, não do cliente final. Registrar no `CLAUDE.md` para ninguém ler a §4.5 como contradição |
| D8 | **Nasce uma página `/privacidade`** pública, curta e factual | O consentimento LGPD precisa apontar para um texto de uso do dado; sem ele o checkbox é decorativo |
| D9 | **Um worktree, uma branch (`feat/landing-leads`), um PR para `develop`**, base `origin/develop` | As subtasks 2 e 3 compartilham o contrato do endpoint; PR único evita front sem back em dev. A develop está à frente da main (#255) e as branches `feat/client-screens-layout` e `fix/client-titles-row-slack` estão pushed e não mergeadas: elas tocam `middleware.ts`/`e2e`; conflito é esperado e se resolve no merge, não agora |
| D10 | Contrato OpenAPI regenerado **sem servidor**: `python -c "from app.main import app; import json; print(json.dumps(app.openapi()))" > openapi.json` e `openapi-typescript openapi.json -o src/lib/contracts/schema.ts` | O sandbox não abre socket para o `gen:types` apontar em `localhost:8000` |
| D11 | Subtasks **4 e 5 não podem ser concluídas** sem o nome; o que dá para fazer nelas está na §9 e §10 e É feito | "Fazer todas as tasks" significa levar cada uma até a fronteira do que não depende de terceiros |

---

## 3. Arquitetura da mensagem (base da subtask 1, `86e3fr9uf`)

### 3.1 Fontes (ler antes de escrever uma linha de copy)

- `/home/phaos93/auditoria-lancamentos/Docs/reunioes/` (3 arquivos, **untracked**: ler por caminho
  absoluto, **não commitar**): `2026-09-02-resumo-geral-reunioes-murilo.md` (dores ranqueadas,
  "o que NÃO é dor", frases literais), `2026-09-01-preparacao-murillo.md` (§ "O frame certo para
  contador"), `2026-09-01-roteiro-demo-murillo.md` (ordem de demonstração = ordem natural de
  "como funciona").
- `Docs/NextSteps/PRD - Próximos Passos-20260615173056.md` (FASE 0 a 5).
- `CLAUDE.md` §1 (fluxo núcleo), §3.15/§4.8 (isolamento por tenant e organização), §4.1/§4.5/§4.7/§4.12
  (cripto por cliente, nada identificável em claro, trilha de acesso, encerramento com crypto-shredding), §5 (cruzamento determinístico, IA nunca decide), §8 (o que cada sprint deixou).
- Transcrições de 04/09, 17/09 e 28/09 **não estão no repositório**. Não inventar conteúdo delas.
  O que se sabe delas está resumido no `CLAUDE.md` e nas fontes acima.

### 3.2 Dores e como a plataforma responde (com a fonte de cada uma)

| Dor (como o público sente) | Fonte | O que existe hoje e sustenta a frase |
|---|---|---|
| O fechamento não roda todo mês; vira um crunch de meses | Murilo, R2 (dores consolidadas nº 1) | Conciliação por conta + mês, com lista, revisão por abas e relatório Excel (S0 a S19, Sprint 4) |
| Dado ruim entra na contabilidade: imobilizado lançado como despesa, "ajuste de saldo" inventado, tarifas somadas errado | Murilo, R1 (as dores nº 2) | Cruzamento determinístico com tolerância de R$ 0,01 e 3 dias, anomalias tipadas, qualificação por IA que **sinaliza** e nunca decide (§5) |
| Categorizar, fazer de-para e montar lançamento é PROCV manual, cliente a cliente | Murilo, R2 (nº 2 e 3) | De-para multi-destino por cliente (S12), plano contábil do cliente e partida derivada do sinal (S16), glossário por cliente (Sprint 6) |
| A maioria dos clientes não tem ERP; chega planilha e extrato em todo formato | Murilo, R1/R2 (6 a 7 de 40 com Omie) | Origem por arquivo com mapeamento de entrada (S14); leitura de extrato e fatura por IA (S9, Sprint 2) |
| Fatura de cartão é trabalho à parte e ninguém lança | Hologram (Sprint 1 e 7) | Conciliação de fatura de cartão e lançamento das compras no Omie (desligado por ambiente; **não prometer** na landing além de "lança no Omie" se `OMIE_POSTING_ENABLED` estiver ligado em dev: descrever como "prepara o lançamento" se não for o caso) |
| Título vencido há meses não aparece na conciliação do mês | Sprint 11 e 15 | Carteira de títulos em aberto sem recorte de mês; contexto do título separa inadimplência real de acordo |
| O cliente final quer ver o próprio dado, e o escritório não pode vazar um cliente para o outro | Sprint 5 e camada de organizações | Acesso do cliente ao próprio tenant; organização só alcança os próprios clientes; decisão única de acesso (§3.15) |
| "Onde meus dados ficam e quem lê?" | 86e3anx75, §4 | Chave de criptografia própria por cliente, arquivo original nunca guardado, trilha de acesso (LGPD), encerramento com destruição da chave |

**Regras da copy:**

- Cada frase de capacidade aponta para algo que está na `main`. Antes de escrever, o executor
  confere `git log origin/main --oneline | head -40` e a §8 do `CLAUDE.md`. O que estiver só em
  branch (ex.: exportador para sistema contábil, Sprint 13) entra como "em validação com
  escritórios parceiros", ou não entra.
- **Nenhum número** (horas economizadas, % de acerto, quantidade de clientes). Não existe medição.
- **Segurança como ela é** (86e3anx75): dizer que o arquivo é lido por um provedor de IA
  (Anthropic) para extrair as movimentações, e que o cruzamento é determinístico. **Não afirmar**
  termos de retenção ou de treinamento do provedor: isso precisa de fonte citada e a task
  86e3anx75 ainda está aberta. Frase segura: "o arquivo é processado por IA apenas para a
  leitura das movimentações e não é armazenado pela plataforma".
- Tom: português do Brasil, direto, frases curtas, segunda pessoa ("seu cliente", "seu
  fechamento"), sem jargão de dev (tenant, endpoint, DEK), sem superlativo, sem travessão
  (usar vírgula, dois-pontos ou frase separada), sem emoji.
- Palavras que não entram: "revolucionário", "inteligente" como adjetivo de marketing,
  "100%", "garantimos", "Auditoria de Lançamentos" (nome antigo), "ADL".

### 3.3 Estrutura da página (ordem e função de cada bloco)

1. **Header** (fixo, fino): `BrandMark` + "Hologram"; à direita, botão secundário **Entrar**
   (`/login`) e botão primário **Entrar em contato** (âncora `#contato`).
2. **Hero:** título (uma frase, a promessa), subtítulo (para quem e o que muda), os dois botões
   repetidos, e ao lado uma vinheta de produto (card de conciliação com badges de status).
   Sugestão de título para o executor refinar: *"O financeiro do seu cliente, conferido antes de
   virar contabilidade."*
3. **Para quem** (3 cards curtos): escritório de contabilidade, BPO financeiro, empresa.
4. **Dores e respostas** (4 pares, do mais caro ao mais barato, saídos da tabela 3.2).
5. **Como funciona** (4 passos, na ordem do fluxo núcleo do §1 do `CLAUDE.md`): envia o arquivo
   ou conecta a origem; a plataforma lê e cruza; a equipe revisa o que ficou de fora; sai o
   relatório e o lançamento.
6. **Segurança e privacidade** (4 itens factuais, tabela 3.2 última linha; link para `/privacidade`).
7. **Formulário de contato** (`#contato`): título, uma linha dizendo o que acontece depois
   ("respondemos pelo e-mail informado"), campos da §5.1, consentimento com link para
   `/privacidade`, botão primário "Enviar".
8. **Rodapé:** "Hologram Gestão", link Entrar, link Aviso de privacidade, ano.

**Entregável versionado da subtask 1:** `Docs/landing/COPY.md` com (a) a tabela 3.2 revisada,
(b) a estrutura acima com o texto final de cada bloco, (c) a lista de afirmações e a evidência
de cada uma (arquivo/sprint), (d) o texto do consentimento e da página de privacidade. O
Lucas revisa esse arquivo; a página lê os textos de um módulo (`landing/content.ts`) para a
revisão dele virar edição de um lugar só.

---

## 4. Design

- **Tokens, nunca hex.** Só classes semânticas (`bg-background`, `bg-card`, `text-foreground`,
  `text-muted-foreground`, `text-primary`, `border`). Texto sobre fundo com alfa é proibido
  (`front-gate` §4). Hover de botão usa os tokens que `ui/button.tsx` já tem.
- **Escopo do tema:** o layout do grupo `(public)` envolve tudo em
  `<div className="hologram bg-background text-foreground min-h-screen">`. O `.hologram` de
  `globals.css` define as variáveis por classe, então o escopo vence o tema do `<html>`, seja
  qual for a escolha salva no `localStorage`. Não renderizar `ThemeToggle`.
- **Largura:** container `max-w-6xl mx-auto px-4 sm:px-6`. É uma exceção deliberada à regra de
  "largura total" do `.claude/design-system.md`, que é do shell autenticado; registrar no
  cabeçalho do layout público.
- **Hierarquia de botões:** um primário por bloco (Entrar em contato / Enviar); Entrar é
  secundário (`variant="outline"` ou `secondary`, conforme o que `ui/button.tsx` oferece com
  contraste ≥ 3:1 no Hologram).
- **Vinhetas de produto:** componentes puros em `components/landing/`, sem dado real e sem
  fetch, reusando `Badge`, `Card`-like (`bg-card border rounded-lg`) e `Table` quando couber.
  Nada de texto que pareça dado de cliente real (usar "Cliente exemplo", valores redondos).
- **Direção visual (D12): moderna, escura, com profundidade e movimento, tudo na marca.**
  Referência de linguagem: landing de produto SaaS B2B atual (Linear, Vercel, Resend): fundo
  navy profundo da Hologram, texto claro, superfícies em vidro, brilhos discretos, muito
  espaço em branco, uma coisa acontecendo por vez. Sofisticado, nunca carnaval. Sem
  biblioteca de animação (`framer-motion` não entra): tudo em CSS e um único componente cliente
  pequeno, `components/landing/reveal.tsx`, com `IntersectionObserver` para revelar blocos na
  rolagem (`'use client'` é permitido nele porque há efeito).
- **Cores da marca, de onde saem:** o navy oficial já está nos tokens do tema `.hologram`
  (`--background`, `--card`, `--primary` branco, `--accent`; ver `globals.css:125` e o rodapé
  v1.16 do `CLAUDE.md`: `#0C0C5A` amostrado do logomark em `Docs/brand/`). Todo texto e todo
  botão usam **só tokens**. Para os efeitos decorativos nasce um bloco de variáveis
  **escopadas à landing** (`.landing { --glow-1: hsl(var(--primary) / 0.18); ... }`), derivadas
  dos tokens existentes, usadas **apenas em fundo decorativo, borda e sombra**, nunca em texto.
  Nenhum hex novo em componente (regra `front-gate` §4).
- **Efeitos, bloco a bloco:**
  - **Hero:** fundo com 2 ou 3 formas radiais (aurora) nas cores da marca, `blur` alto,
    drift lento por `@keyframes` (20 a 40 s, `will-change: transform`); grade sutil ou ruído
    via `background-image` CSS (sem asset externo); título e subtítulo entram com fade-up
    escalonado (`animation-delay` de 80 ms entre linhas); a vinheta de produto ao lado entra
    depois, com leve inclinação em perspectiva (`rotateX/rotateY` de 2 a 4 graus) que zera no
    hover.
  - **Vinheta de produto animada:** um card de conciliação com 4 ou 5 linhas fictícias em que
    os badges viram de "Pendente" para "Conciliado" em sequência, em loop lento (CSS
    `animation` com `steps`), e um contador de saldo que "bate". É a demonstração do produto
    sem vídeo e sem screenshot.
  - **Cards (para quem, dores, segurança):** superfície de vidro (`bg-card/60`
    `backdrop-blur` só em FUNDO de card, com o texto sobre a área sólida do card),
    borda `border-white/10` no Hologram, hover com `translateY(-2px)`, sombra e borda com
    o brilho da marca; ícones `lucide-react` em pastilha com gradiente da marca.
  - **Como funciona:** 4 passos ligados por uma linha que se desenha na rolagem
    (`stroke-dashoffset` animado quando o bloco entra) ou, se ficar caro, passos numerados
    com revelação escalonada.
  - **Header:** fixo, transparente no topo, ganha `backdrop-blur` e borda inferior ao rolar
    (classe trocada pelo mesmo `reveal.tsx` ou por um `useEffect` de scroll no header cliente).
  - **Formulário:** foco com anel na cor da marca, botão primário com brilho no hover, estado
    de sucesso com ícone animado (check que se desenha).
- **Limites dos efeitos (o que separa moderno de cansativo):** duração de entrada entre
  300 e 700 ms, `ease-out`; nada pisca nem gira; nenhum efeito em texto corrido; **tudo
  desligado sob `@media (prefers-reduced-motion: reduce)`** (as animações viram estado final,
  a aurora fica parada); LCP é texto, não imagem; a página não carrega JS de animação além do
  `reveal.tsx`. Performance: `next build` sem aviso de bundle, e `pnpm --filter @auditoria/web
  build` continua verde.
- **Contraste sob efeito:** o gate de a11y mede com o ponteiro EM CIMA dos botões e cards
  (`hover()` explícito antes do `analyze`, regra do `CLAUDE.md` v1.34) e mede o header nos
  dois estados (topo e rolado). Vidro e brilho ficam fora de baixo do texto; se o axe devolver
  `incomplete` por fundo translúcido, medir com `contrasteComposto` como o e2e já faz.
- **Prints obrigatórios para o aceite visual:** hero no topo (com a vinheta), meio da página
  com um card em hover, formulário, estado de sucesso, e 390px descrito no README. O Pedro e
  o Lucas aprovam a estética pelo link em dev; ajustes de gosto viram edição, não retrabalho
  de estrutura, porque texto está em `content.ts` e efeitos em CSS escopado.
- **Responsivo:** 390px sem rolagem horizontal, sem texto cortado, botões do header cabendo
  (abaixo de `sm`, esconder o rótulo "Hologram" e manter a marca, como o header do app já faz).
- **Acessibilidade:** landmarks (`header`, `main`, `nav aria-label`, `footer`), um `h1`, `h2` por
  bloco, `aria-describedby` nos erros do formulário, honeypot fora da árvore acessível
  (`aria-hidden`, `tabIndex={-1}`, `autoComplete="off"`), foco visível, sem `title` nativo.
- **Metadados:** `title`, `description`, `openGraph` (title, description, locale `pt_BR`,
  type `website`), `robots: { index: true, follow: true }` **sobrescrevendo** o `index: false`
  do layout raiz. `apps/web/src/app/robots.ts` liberando `/` e `/privacidade` e bloqueando o
  resto. `opengraph-image.tsx` gerado (P2, só se sobrar tempo).

---

## 5. Backend (subtask 2, `86e3fr9ut`)

### 5.1 Modelo `leads` (migration reversível, skill `migration`)

| Coluna | Tipo | Regra |
|---|---|---|
| `id` | UUID pk | `uuid4` |
| `name` | `String(120)` NOT NULL | trim; 2 a 120 |
| `email` | `String(254)` NOT NULL | trim; `EmailStr` (dep `email-validator` já existe) |
| `company` | `String(120)` NULL | opcional |
| `whatsapp` | `String(20)` NULL | opcional; só dígitos, `+`, espaço, `(`, `)`, `-` |
| `message` | `String(1000)` NULL | opcional |
| `consent_at` | `TIMESTAMPTZ` NOT NULL | momento do consentimento (evidência LGPD), `now()` do servidor |
| `consent_text_version` | `String(20)` NOT NULL | versão do texto exibido (ex.: `2026-09-29`), constante no código |
| `source` | `String(30)` NOT NULL default `'landing'` | origem do lead |
| `notified_at` | `TIMESTAMPTZ` NULL | quando o Slack aceitou |
| `created_at` | `TIMESTAMPTZ` NOT NULL default `now()` | |

Índice `ix_leads_email_lower_created_at` em `(lower(email), created_at)` para o limite por
e-mail. Sem FK para `clients`/`organizations`/`users`. Sem IP, sem user agent, sem cookie.
Constantes de tamanho no modelo (`LEAD_NAME_MAX = 120`, etc.) reusadas pelo schema, com teste
que amarra as duas (regra §7 Backend: o limite do schema é o da coluna).

### 5.2 Endpoint

- `POST /api/v1/leads`, **sem autenticação**, módulo `apps/api/app/modules/leads/`
  (`routes.py / service.py / repository.py / schemas.py / notifier.py`), registrado em
  `main.py` como os demais.
- Request: `{ name, email, company?, whatsapp?, message?, consent: true, website?: "" }`
  (`website` é o honeypot; `consent` é `Literal[True]`).
- Response de sucesso: **200** `{ "data": { "received": true } }`. A mesma resposta para
  honeypot preenchido e para e-mail no limite: quem abusa não distingue.
- Erros: 400 `VALIDATION_ERROR` genérico do handler global para forma inválida; 429 do
  `slowapi` no envelope padrão.
- `NON_TENANT_ENDPOINTS` ganha `"POST /api/v1/leads": "público; captação de lead da landing; não lê nem grava dado escopável"`.
  Regenerar `apps/api/docs/endpoints-sensiveis-sprint5.md` com
  `apps/api/scripts/gen_sensitive_endpoints_doc.py`. A lista canônica de sensíveis **continua 108**.

### 5.3 Fluxo do serviço (nesta ordem)

1. `website` preenchido → retorna sucesso, não grava, não notifica, loga só
   `lead_honeypot_hit` sem campo nenhum.
2. Limite por e-mail: `count(leads where lower(email) = lower(:email) and created_at > now() - 24h) >= 3`
   → retorna sucesso, não grava, não notifica, loga `lead_throttled` sem campo nenhum.
3. Grava o lead (`consent_at = now()`, `consent_text_version = CONSENT_TEXT_VERSION`).
4. Notifica o Slack (§5.5) com timeout 3 s; se aceitou, `notified_at = now()` na mesma transação.
5. Evento `lead_recebido` em `usage_events` (fail-soft, `_props_or_none`), props só com
   `source`, `has_company`, `has_whatsapp`, `has_message`, `notified` (booleanos). Evento novo
   nasce **sem dedup** (regra do projeto).

### 5.4 Anti-spam e limite

- `@limiter.limit("10/minute")` no handler, chave `get_remote_address`. Comentário no código
  dizendo que atrás do BFF isso é **um teto global por instância** (86e3anx10) e que a defesa
  principal é o honeypot + o limite por e-mail. O número é baixo o bastante para um abuso não
  inundar o Slack e alto o bastante para o evento (dezenas de visitantes, poucos formulários).
- Texto do 429 na tela: "Muitas mensagens em pouco tempo. Tente de novo em um minuto."

### 5.5 Slack

- Setting nova `LEADS_SLACK_WEBHOOK_URL: SecretStr | None` em `core/config.py`, com docstring
  dizendo que **não** entra em `has_webhook_alert`/`has_alert_channel` (mesmo motivo do
  `ALERT_WEBHOOK_URL_SYNTHETIC`, §3.14 do `CLAUDE.md`). Sem a setting, o serviço grava e loga
  `lead_notification_skipped` uma vez por lead, sem a URL.
- Payload de incoming webhook (`text` + `blocks` opcionais), com `&`, `<`, `>` escapados
  (exigência do mrkdwn do Slack), mensagem truncada em 500 caracteres. Campos: nome, e-mail,
  empresa, WhatsApp, mensagem, data/hora em `America/Sao_Paulo`.
- Falha (timeout, não-2xx, exceção) → log `lead_notification_failed` com **só** a categoria
  (`timeout` / `http_<status>` / `transport`). Nunca a URL, nunca campo do lead. Confirmar que
  `SENSITIVE_KEY_TERMS` do redactor (`core/logging.py:45`) cobre `webhook`/`url`; se não cobrir,
  incluir.
- Deploy: secret `leads-slack-webhook-url-${ENV}` em `scripts/setup-gcp.sh` (mesmo padrão de
  `alert-webhook-url-${ENV}`, criada com uma versão vazia para o `--update-secrets ...:latest`
  resolver), `LEADS_SLACK_WEBHOOK_URL=...:latest` nas **4** ocorrências de `--update-secrets` em
  `.github/workflows/deploy-dev.yml` (e no `deploy-prod.yml` se ele tiver o mesmo bloco),
  entrada em `apps/api/.env.example` e parágrafo em `scripts/environments-runbook.md`. ⚠️ A
  secret precisa existir no GCP **antes** do deploy que a referencia, senão o deploy falha.

### 5.6 Testes do backend

- Unitários: limites do schema amarrados às constantes do modelo; `consent` falso é 400;
  honeypot não grava e não notifica; limite por e-mail no 4º envio; notificador escapa mrkdwn e
  trunca; **nenhum campo do lead nem a URL em log**, provado com
  `structlog.testing.capture_logs` (o `caplog` não vê o structlog); fail-soft (Slack fora →
  lead gravado, `notified_at` nulo).
- Integração (testcontainers ou `TEST_DATABASE_URL`): POST cria linha com `consent_at`;
  honeypot → 200 sem linha; classificação do endpoint no `test_sensitive_endpoints.py`
  (a rota nova tem de estar em `NON_TENANT_ENDPOINTS` ou o teste de cobertura falha); ciclo
  `alembic upgrade head` → `downgrade -1` → `upgrade head`.
- Se o Docker não estiver disponível, o handoff diz "escrito e **não executado**" para cada
  teste de integração, com o comando para rodar (regra §7 Backend).

---

## 6. Frontend (subtask 3, `86e3fr9vz`)

### 6.1 Rotas e middleware

- Novo grupo `apps/web/src/app/(public)/` com `layout.tsx` (escopo `.hologram`, header e rodapé
  públicos), `page.tsx` (a landing, substitui `app/page.tsx`, que é apagado) e
  `privacidade/page.tsx`.
- `middleware.ts`: constante `PUBLIC_PATHS = ['/', '/privacidade']`. Sem cookie → `next()`
  nessas rotas; com cookie, `/` → redirect `/clientes` (decisão do Pedro), `/privacidade` →
  `next()`. Todo o resto continua igual. Atualizar o docstring do arquivo (ele diz que `/login`
  é a única rota pública).
- Teste novo `src/__tests__/middleware.test.ts` (não existe hoje) cobrindo: `/` sem cookie
  passa; `/` com cookie vai para `/clientes`; `/login` com cookie vai para `/clientes`;
  `/clientes` sem cookie vai para `/login`; `/privacidade` passa nos dois casos.

### 6.2 Componentes

```
apps/web/src/components/landing/
  landing-header.tsx      server; BrandMark + Entrar + Entrar em contato
  landing-hero.tsx        server
  landing-audience.tsx    server; 3 cards
  landing-pains.tsx       server; 4 pares dor/resposta
  landing-how.tsx         server; 4 passos
  landing-security.tsx    server
  landing-footer.tsx      server
  product-vignette.tsx    server; vinheta de conciliação (dado fictício)
  contact-form.tsx        'use client'; react-hook-form + zod
  content.ts              TODO o texto da página (a copy do Lucas edita aqui)
```

Server components por padrão; `'use client'` só no `contact-form.tsx`.

### 6.3 Formulário

- Schema zod em `src/lib/validation/lead.ts`, com os **mesmos limites** do backend
  (`name` 2..120, `email` ≤ 254, `company` ≤ 120, `whatsapp` ≤ 20, `message` ≤ 1000,
  `consent: z.literal(true)`, `website: z.string().max(0)`).
- Chamada: ler `src/lib/api/client.ts` antes. Se `apiPost` tentar refresh de token ou
  redirecionar para `/login` em 401, **não** usá-lo: criar `src/lib/api/leads.ts` com `fetch`
  direto em `/api/v1/leads` (o rewrite do BFF já cobre `/api/v1/*`), reaproveitando o parser
  de envelope de erro (`ApiError`) para `userMessage`.
- Estados: idle → enviando (botão desabilitado + spinner, `aria-busy`) → sucesso (o formulário
  é substituído por uma confirmação com `role="status"`: "Recebemos sua mensagem. Vamos
  responder pelo e-mail informado.") ou erro (alerta inline `role="alert"`, botão reabilitado;
  429 com o texto da §5.4; rede fora com o `NetworkError.userMessage`).
- Consentimento: checkbox obrigatório com o texto curto e link para `/privacidade` abrindo em
  nova aba com `rel="noopener"`.
- Honeypot: campo `website` visualmente escondido (classe `sr-only`-like absoluta fora da
  tela, **não** `display:none`, para bots que ignoram CSS oculto preencherem), `aria-hidden`,
  `tabIndex={-1}`, `autoComplete="off"`.

### 6.4 Testes do front

- vitest (`components/landing/__tests__/contact-form.test.tsx`): renderiza com honeypot fora
  da árvore acessível; consentimento desmarcado bloqueia; sucesso troca para a confirmação;
  429 mostra o texto certo; erro reabilita o botão; `content.ts` não contém "Auditoria de
  Lançamentos" nem "ADL" (teste de string, trava a D2).
- `theme-contrast.test.ts`: só se nascer par novo de cor (não deve nascer).
- e2e `a11y-mocked.spec.ts`: cenários novos **landing desktop e mobile** (axe sem
  `critical`/`serious`, incluindo `hover()` explícito nos dois botões do header),
  **formulário em estado de erro**, **página de privacidade**; mock do `POST /api/v1/leads`
  com `page.route`. Guarda geométrica: sem rolagem horizontal em 390px
  (`document.documentElement.scrollWidth <= innerWidth`).
- Gate: `pnpm --filter @auditoria/web lint`, `type-check`, `test` (no worktree do agent, com
  `--cache=false`), `build`, e o gate de a11y nos 3 temas em container (receita na skill
  `front-gate`; o `docker` pode estar indisponível, ver §11).
- Prints: `screenshots/pr-shots-86e3fr9vz/` com **desktop, tema hologram** da landing (topo,
  formulário, sucesso) e da privacidade, mais `README.md`; o 390px é conferido e descrito no
  README, sem salvar o PNG.

---

## 7. Segurança e privacidade (checklist de revisão)

- [ ] `LEADS_SLACK_WEBHOOK_URL` só em env/Secret Manager; nunca em log, resposta ou teste com valor real.
- [ ] Nenhum campo do lead em log (provado por `capture_logs`).
- [ ] Endpoint público classificado em `NON_TENANT_ENDPOINTS` com motivo; sensíveis seguem 108/108.
- [ ] Resposta de sucesso não ecoa os dados; honeypot e limite respondem igual ao sucesso.
- [ ] `consent: Literal[True]` no servidor; `consent_at` e `consent_text_version` gravados.
- [ ] Slack: `& < >` escapados; mensagem truncada.
- [ ] CSP intocada (nada externo: sem font, script, imagem ou CAPTCHA de terceiro).
- [ ] `/privacidade` diz: quais dados, para quê (contato comercial), base legal (consentimento), quem lê (equipe da Hologram, canal interno), por quanto tempo (até o pedido de exclusão ou 12 meses sem contato), como pedir exclusão (e-mail da Hologram, a confirmar com o Pedro; até lá, "pelo mesmo formulário").
- [ ] A copy de segurança não afirma retenção/treinamento do provedor de IA (86e3anx75 aberta).
- [ ] `CLAUDE.md` ganha a nota da D7 na §4 e a rota nova na §3.15 (`NON_TENANT_ENDPOINTS` 14 → 15).

---

## 8. Ordem de execução e commits

Um commit por marco, Conventional Commits em EN-US, corpo dizendo o que e por quê:

1. `docs(landing): add the execution plan for the public landing page` (este arquivo copiado
   para o worktree; **primeiro commit** da branch).
2. `docs(landing): write the copy with pains, answers and their sources` (`Docs/landing/COPY.md`).
3. `feat(api): add public lead capture with honeypot, e-mail throttle and Slack notice`
   (modelo, migration, módulo, settings, lista canônica, doc regenerada, testes, deploy/secret).
4. `feat(web): centralize the brand name in one constant` (subtask 5, parte executável; sem
   mudança visual).
5. `feat(web): add the public landing page with contact form and privacy notice` (grupo
   `(public)`, middleware, componentes, form, validação, testes, e2e, robots).
6. `docs(landing): record the domain and product name checklist` (subtask 4, §9).
7. `docs(primer): record the public landing, the leads table and the brand constant` (`CLAUDE.md`).

Contrato OpenAPI regenerado (D10) entra no commit 3 (o schema TS muda junto com a API).

---

## 9. Subtask 4 (infra, `86e3fr9wm`): o que dá para fazer sem o nome

Entregável `Docs/landing/DOMINIO_E_NOME.md`:

1. **Checklist técnico do domínio próprio no Cloud Run** em `southamerica-east1`: se o
   mapeamento de domínio do Cloud Run está disponível na região ou se é preciso um Load
   Balancer HTTPS com certificado gerenciado; efeito no cookie (o BFF mantém o cookie na
   origem do front, então um domínio único para o web basta; a API continua interna via
   `INTERNAL_API_URL`); efeito no CORS (`allowed_origins_list` em `core/config.py`); env vars
   do front e da API que carregam URL; links em alertas e no e-mail de "esqueci minha senha"
   (86e3fr9xz). **Cada afirmação sobre o Cloud Run vem da documentação oficial com o link, ou
   fica marcada "(a confirmar)".** Se o executor não tiver rede, marca tudo como "a confirmar"
   e lista o que conferir.
2. **Critérios para o nome** (do épico: englobar categorização para o sistema contábil e as
   outras funções financeiras; do Slack: "Olo Finance" não pegou) e **10 a 15 sugestões**
   com o raciocínio de cada uma, marcadas "disponibilidade de domínio a confirmar".
3. **Roteiro pós-decisão**, passo a passo: compra, DNS, mapeamento, certificado, atualização
   das env vars, `--update-env-vars` sem deploy, teste do cookie e do login, remoção do
   `robots noindex`, atualização do `brand.ts`/`branding.py` (subtask 5).

Estimativa e o comentário de fechamento entram no ClickUp; a task **fica aberta** (bloqueada
pelo nome) e o executor diz isso no handoff.

---

## 10. Subtask 5 (rename, `86e3fr9x3`): o que dá para fazer sem o nome

- `apps/web/src/lib/brand.ts`: `PRODUCT_NAME = 'Auditoria de Lançamentos'` (valor atual,
  **sem mudança visual**), `COMPANY_NAME = 'Hologram Gestão'`, `PRODUCT_TAGLINE`.
  Usar em `app/layout.tsx` (metadata), `(app)/layout.tsx`, `(auth)/login/page.tsx` e no
  header do app, onde hoje há string literal.
- `apps/api/app/core/branding.py`: `PRODUCT_NAME`, usado em `main.py` (título do OpenAPI) e
  `app/__init__.py`.
- Teste que garante que a string literal "Auditoria de Lançamentos" só existe nesses dois
  arquivos de marca (grep em `apps/web/src` e `apps/api/app`, excluindo os dois) para a
  troca futura ser de uma linha.
- A task **fica aberta** até o nome existir; o comentário no ClickUp diz que a troca virou
  uma linha por lado.

---

## 11. Ambiente, ferramentas e armadilhas conhecidas (ler antes de rodar qualquer comando)

- **Worktree próprio:** `git -C /home/phaos93/auditoria-lancamentos fetch origin && git -C /home/phaos93/auditoria-lancamentos worktree add /home/phaos93/auditoria-landing -b feat/landing-leads origin/develop`.
  Outra sessão pode estar usando o checkout principal; nunca trocar a branch dele. Todo git
  com `-C <caminho>`; o `cd` de um comando Bash vaza para o seguinte.
- **Backend:** `uv run --extra dev <cmd>` (sem `--extra dev` o venv perde ruff/mypy). Chaves
  fake do CI exportadas (`DATABASE_URL`, `OMIE_ENCRYPTION_KEY`, `JWT_SECRET`,
  `SEARCH_BLIND_INDEX_KEY` com 64 hex, `ANTHROPIC_API_KEY=sk-ant-ci-fake`,
  `ENVIRONMENT=development`). Se o venv nascer em Python 3.14, criar um 3.12 fora do worktree
  com `UV_PROJECT_ENVIRONMENT`. Gate: `ruff check . && ruff format --check . && mypy app/ && pytest -q --no-cov`.
  Três testes de `test_alerting.py` quebram só local quando existe `apps/api/.env`; no worktree
  não há `.env`, então devem passar.
- **Integração:** exige Docker. Testar `docker ps` **antes** e pedir ao Pedro para ligar, no
  começo, sem parar o trabalho. Com Docker: `TEST_DATABASE_URL` no `auditoria-postgres`
  (porta 5433) ou testcontainers em container (`--network host`). Sem Docker: unitários rodam,
  integração fica declarada como não executada.
- **Front:** `pnpm --filter @auditoria/web lint | type-check | build`; vitest com
  `pnpm --filter @auditoria/web exec vitest run --cache=false` no worktree (o `node_modules`
  pode ser link só-leitura). Chromium do host não sobe: gate de a11y em container
  `mcr.microsoft.com/playwright:v1.59.1-noble` com `-u "$(id -u):$(id -g)" -e HOME=/tmp`,
  build no host, servidor standalone em 3100, `--retries=0`. `E2E_SHOTS=1` só na rodada do
  `hologram`.
- **Contrato:** D10 (dump do `app.openapi()` para arquivo, `openapi-typescript` no arquivo).
- **ClickUp:** ao iniciar, marcar 1, 2 e 3 como `IN PROGRESS` e gravar a estimativa inicial
  (`time_estimate`) das 5 subtasks com os valores da §12; ao terminar, substituir pelo tempo
  real de cada uma (fonte A: criação da branch → último commit do marco correspondente,
  proporcional) e escrever um comentário curto por subtask com o que foi feito e o que ficou.
  Criar a subtask **6. [DEPLOY] Publicar a landing em dev e mandar o link ao Lucas** no épico,
  com o checklist da §13 (deploy é sempre task separada). Nunca mover para `done`.
- **Nunca** `git push`, `gh pr create`, `--no-verify`, `--force`. Ao fim, entregar os comandos
  prontos: `git push -u origin feat/landing-leads` e `gh pr create --base develop --head
  feat/landing-leads` com título e corpo em português.
- **Screenshots** em `/home/phaos93/auditoria-lancamentos/screenshots/pr-shots-86e3fr9vz/`
  (pasta ignorada pelo git), nunca em `/tmp`.
- **`CLAUDE.md`:** a versão nova é `(maior versão em origin/develop) + 1`; conferir com
  `git -C <worktree> show origin/develop:CLAUDE.md | grep -m1 '^_Versão'` na hora de editar,
  porque outras branches abertas também incrementam o rodapé.
- **Memória do projeto:** ao fim, gravar um arquivo em
  `/home/phaos93/.claude/projects/-home-phaos93-auditoria-lancamentos/memory/landing-leads-entrega.md`
  (frontmatter `type: project`, o que foi feito com números verificáveis, o que ficou) e uma
  linha no `MEMORY.md` dali.

---

## 12. Estimativas iniciais (para o `time_estimate` do ClickUp)

Base: o épico de layout das telas do cliente (`86e3fr9pd`, 4 tasks de front) levou ~100 min em
29/09/2026; cada subtask do épico de lista de clientes levou ~25 min; o backend novo aqui tem
migration, módulo e integração com serviço externo.

| Subtask | Estimativa | O que pesa |
|---|---|---|
| 1 copy | 45 min | leitura das fontes + `COPY.md` + `content.ts` |
| 2 back | 90 min | migration, módulo, notificador, testes, deploy/secret, contrato |
| 3 front | 180 min | 10 componentes, efeitos da §4 (aurora, vinheta animada, reveal, vidro), form, middleware + teste, e2e com hover, gate em container, prints |
| 4 infra | 30 min | documento de checklist e nomes |
| 5 rename | 30 min | constantes, substituições, teste de grep |
| **Total** | **6 h 15** | é estimativa; o tempo real substitui no fechamento |

---

## 13. Definição de pronto e handoff

A execução termina quando **tudo** abaixo for verdade:

1. Sete commits da §8 na branch `feat/landing-leads`, gate local rodado com output citado
   (números reais de ruff/mypy/pytest/vitest/a11y).
2. `Docs/landing/COPY.md`, `Docs/landing/DOMINIO_E_NOME.md`, `CLAUDE.md` atualizados.
3. Prints em `screenshots/pr-shots-86e3fr9vz/` com README.
4. ClickUp: 1, 2, 3 em `IN PROGRESS` com tempo real no estimado e comentário; 4 e 5 com
   estimativa, comentário e o motivo de seguirem abertas; subtask 6 (deploy) criada com o
   checklist: criar `leads-slack-webhook-url-dev` no Secret Manager (alguém com acesso ao
   Slack cria o incoming webhook do canal INV-ADL), rodar `scripts/setup-gcp.sh dev` ou o
   `gcloud secrets create` equivalente, mergear develop → main, conferir `/` em dev sem cookie
   e com cookie, enviar um lead de teste, ver a mensagem no Slack, mandar o link ao Lucas.
5. Memória gravada (§11).
6. **Mensagem final** no formato da §12 do `CLAUDE.md`: (1) resumo executivo com arquivos,
   `git diff --stat`, hashes e status do gate; (2) passo a passo de teste manual com caminho
   feliz e de erro (honeypot, 429, consentimento desmarcado, `/` com cookie); (3) o que **não**
   foi testado e por quê; (4) o que depende do Pedro (webhook, secret, Docker se faltou, revisão
   do Lucas na copy, imagens do Magnific); (5) os comandos de push e PR prontos, com o corpo do
   PR em português.

**O que a revisão (sessão de planejamento) vai olhar** depois do handoff: cada item da §7;
`content.ts` contra a tabela 3.2 (toda afirmação tem evidência na `main`?); o PNG desktop e o
relato do 390px; `middleware.test.ts`; o teste de log com `capture_logs`; a classificação em
`NON_TENANT_ENDPOINTS`; o diff do `CLAUDE.md`; os tempos no ClickUp.

# Domínio próprio e nome do produto (subtask 86e3fr9wm)

> **Para quem é:** o Pedro e quem for executar a troca de domínio. **Estado (30/09/2026):** o
> nome está **decidido**: o produto se chama **Hologram OS** e o domínio será
> **`hologramos.com.br`**. O nome já está no código (subtask 86e3fr9x3). O domínio **ainda não
> foi registrado** e não aponta para nada: falta registrar e montar o Load Balancer (seção 3).
> A empresa continua **Hologram Gestão**.
>
> Regra deste documento: toda afirmação sobre o Google Cloud vem da documentação oficial,
> com o link; o que não foi conferido está marcado **(a confirmar)**.

---

## 1. Checklist técnico do domínio no Cloud Run

### 1.1 Como apontar um domínio para o serviço web em `southamerica-east1`

A documentação oficial lista três caminhos
([Mapping custom domains](https://docs.cloud.google.com/run/docs/mapping-custom-domains),
consultada em 29/09/2026):

| Caminho                                        | Situação na doc                                            | Serve para nós?                                                                                                                                                                                                                                                                                                           |
| ---------------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Load Balancer de aplicativo externo global** | "Recommended"                                              | **Sim, é o caminho.**                                                                                                                                                                                                                                                                                                     |
| Mapeamento de domínio do próprio Cloud Run     | "Limited availability and Preview"; "not production-ready" | **Não.** A lista de regiões atendidas é asia-east1, asia-northeast1, asia-southeast1, europe-north1, europe-west1, europe-west4, us-central1, us-east1, us-east4 e us-west1. `southamerica-east1` não está nela, e a doc diz que "to map custom domains in other regions, you must use one of the other mapping options". |
| Firebase Hosting                               | listado como opção                                         | Possível, mas coloca um segundo produto (Firebase) na frente do Next; não recomendado sem motivo **(a confirmar se alguém quiser seguir por aqui)**.                                                                                                                                                                      |

O que o caminho do Load Balancer implica, e ainda precisa ser conferido na hora de montar:

- um **serverless NEG** apontando para o serviço web (`auditoria-web-<env>`) **(a confirmar: nome
  exato do serviço em cada ambiente)**;
- um **IP global** reservado e o registro `A` do domínio apontando para ele;
- **certificado gerenciado pelo Google** no Load Balancer (a doc cita que no Load Balancer dá
  para usar certificado próprio; o gerenciado é o padrão do Google para esse caso)
  **(a confirmar: tempo de emissão e validação por DNS)**;
- **custo mensal** do Load Balancer (regra de encaminhamento + tráfego), que hoje não existe na
  conta **(a confirmar na calculadora do GCP antes de decidir)**;
- opcional: Cloud Armor no mesmo Load Balancer, que resolveria de quebra o limite por IP da
  landing (hoje a API vê o IP do proxy, 86e3anx10) **(a confirmar)**.

### 1.2 Efeitos no sistema (verificados no código em 29/09/2026)

| Onde                                | Hoje                                                                                                                                                                                                                         | Com domínio próprio                                                                                                                                                                                                                                                         |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Cookie de sessão**                | O navegador só fala com o web; o Next faz de proxy reverso para `/api/v1/*` (`apps/web/next.config.mjs`, `rewrites`), então o cookie HttpOnly nasce na origem do web. `COOKIE_DOMAIN` vazio (`apps/api/app/core/config.py`). | **Nada muda no código.** Basta o domínio apontar para o web. Deixar `COOKIE_DOMAIN` vazio: o cookie fica no host exato. Quem estiver logado pela URL `*.run.app` precisa entrar de novo pelo domínio novo (cookie é por host).                                              |
| **Chamada web → API**               | `INTERNAL_API_URL` é **build-arg** do Next (`deploy-dev.yml`, "INTERNAL_API_URL é build-arg"); a API continua na própria URL `*.run.app`.                                                                                    | Nada muda: a API não precisa de domínio.                                                                                                                                                                                                                                    |
| **Host aceito pela API**            | `ALLOWED_HOSTS` (`TrustedHostMiddleware`) com o host da API.                                                                                                                                                                 | Nada muda enquanto a API ficar no `*.run.app`: quem bate nela é o proxy do web.                                                                                                                                                                                             |
| **CORS**                            | `ALLOWED_ORIGINS` / `FRONTEND_URL` (`core/config.py`).                                                                                                                                                                       | Incluir `https://<domínio>` em `ALLOWED_ORIGINS` e trocar `FRONTEND_URL`. Com o proxy, o navegador não faz chamada cruzada, mas o valor precisa refletir a origem real **(a confirmar onde `FRONTEND_URL` é lido: hoje nenhum módulo de `app/` o usa além da declaração)**. |
| **Links em alertas**                | Alertas levam só código e contexto (`core/alerting.py`), sem URL do web.                                                                                                                                                     | Nada a trocar hoje.                                                                                                                                                                                                                                                         |
| **E-mail de "esqueci minha senha"** | Não existe envio por e-mail (a tela de login diz para falar com o administrador, 86e2u5140).                                                                                                                                 | Quando existir (86e3fr9xz), remetente e links no domínio novo.                                                                                                                                                                                                              |
| **`robots`**                        | O layout raiz marca o app como `noindex`; a landing e `/privacidade` sobrescrevem com `index`, e `robots.txt` só libera essas duas.                                                                                          | Nada muda. Conferir no Search Console depois da troca.                                                                                                                                                                                                                      |
| **Metadados da landing**            | `metadataBase`, canônica e `sitemap` só existem com `NEXT_PUBLIC_SITE_URL` (`apps/web/src/lib/site-url.ts`); hoje a variável não existe e a página sai sem URL absoluta.                                                     | Passar `NEXT_PUBLIC_SITE_URL=https://hologramos.com.br` como build-arg (seção 3, passo 7). Nenhuma linha de código muda.                                                                                                                                                    |

---

## 2. Decidido: Hologram OS, `hologramos.com.br`

Decisão do Pedro em 30/09/2026:

- **Produto:** Hologram OS. Cobre o produto inteiro (conciliação, revisão, de-para, arquivo
  contábil, carteira de títulos), que era o critério que "Auditoria de Lançamentos" não
  atendia.
- **Empresa:** continua **Hologram Gestão**, a primeira organização da plataforma. É ela quem
  trata o dado do formulário da landing (aviso de privacidade).
- **Domínio:** `hologramos.com.br`, **ainda não registrado**. Conferir a disponibilidade no
  `registro.br` antes de qualquer outro passo.
- **No código (86e3fr9x3, feito):** `PRODUCT_NAME = 'Hologram OS'` e `PRODUCT_DOMAIN =
'hologramos.com.br'` em `apps/web/src/lib/brand.ts`, `PRODUCT_NAME = "Hologram OS"` em
  `apps/api/app/core/branding.py`. A landing passou a nomear o produto (a D2 do plano caiu).
  O domínio **não** é usado para montar URL: a URL absoluta vem só de `NEXT_PUBLIC_SITE_URL`.

---

## 3. Roteiro para colocar `hologramos.com.br` no ar

1. **Registrar `hologramos.com.br`** no `registro.br`, no nome da Hologram Gestão.
2. **Reservar o IP global** e montar o Load Balancer HTTPS externo com serverless NEG para o
   serviço web do ambiente (seção 1.1). Começar por **dev**.
3. Criar o **certificado gerenciado** para `hologramos.com.br` e esperar o status ativo.
4. Apontar o **DNS** (`A` de `hologramos.com.br` para o IP do Load Balancer) e esperar a
   propagação.
5. Atualizar as **env vars da API** sem deploy novo:
   `gcloud run services update <api> --update-env-vars=FRONTEND_URL=https://hologramos.com.br,ALLOWED_ORIGINS=https://hologramos.com.br`
   (o `ALLOWED_ORIGINS` é CSV: manter a URL `*.run.app` do web junto durante a transição).
6. **Testar o cookie e o login** por `https://hologramos.com.br`: entrar, navegar, sair;
   conferir no DevTools que o cookie `access_token` está no host novo, HttpOnly e Secure.
7. **Ligar a URL absoluta da landing** (`metadataBase`, canônica, `sitemap.xml` e a linha
   `Sitemap:` do `robots.txt`). É `NEXT_PUBLIC_`, então vale o valor do **build**, não do
   runtime, e entra pelo mesmo caminho do `INTERNAL_API_URL`:
   - `docker/Dockerfile.web`, antes do `RUN pnpm --filter @auditoria/web build`:
     `ARG NEXT_PUBLIC_SITE_URL=` e `ENV NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL}` (vazio
     por padrão = comportamento de hoje);
   - `docker/cloudbuild/cloudbuild-web.yaml`: mais um par `'--build-arg',
'NEXT_PUBLIC_SITE_URL=${_SITE_URL}'` e `_SITE_URL: ''` em `substitutions`;
   - `.github/workflows/deploy-dev.yml`, no passo que chama o `cloudbuild-web.yaml`:
     `--substitutions=_TAG=dev,_API_URL=...,_SITE_URL=${{ vars.SITE_URL_DEV }}`, com a variável
     de repositório `SITE_URL_DEV=https://hologramos.com.br`.
     **Só no dia em que o domínio apontar:** com a variável e sem o domínio, a canônica e o
     sitemap ensinariam o buscador um endereço que não abre. Valor sem `https://` estoura o
     build de propósito (`lib/site-url.ts`).
8. Conferir `https://hologramos.com.br/robots.txt`, `/sitemap.xml` e o Open Graph (um
   validador de cartão de link) pelo domínio novo, e cadastrar o domínio no Search Console.
9. Repetir 2 a 8 em **prod** quando o ambiente existir.

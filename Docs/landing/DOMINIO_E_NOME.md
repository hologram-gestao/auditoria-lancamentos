# Domínio próprio e nome do produto (subtask 86e3fr9wm)

> **Para quem é:** o Pedro e o Lucas, que decidem o nome, e quem for executar a troca de
> domínio depois. **Estado:** a task segue **bloqueada pelo nome**. Este documento leva a task
> até onde dá sem ele: o que precisa ser verdade na infraestrutura, os critérios e as
> sugestões de nome, e o roteiro para depois da decisão. Escrito em 29/09/2026.
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
| **Metadados da landing**            | Sem URL absoluta (`metadataBase` não definido).                                                                                                                                                                              | Definir `metadataBase` com o domínio novo quando existir, para o Open Graph sair com URL absoluta.                                                                                                                                                                          |

---

## 2. Nome do produto

### 2.1 Critérios

Do épico e da conversa no grupo da ADL:

1. **Cobrir o produto inteiro**: categorização e de-para para o sistema contábil, conciliação,
   revisão, lançamento, carteira de títulos. "Auditoria de Lançamentos" descreve só uma parte.
2. **Falar com contador e com BPO**, os dois públicos do evento de 30/09.
3. **Curto e pronunciável em português**; funciona dito em voz alta numa reunião.
4. **Convive com a marca Hologram** (a Hologram é a primeira organização da plataforma, não a
   dona do nome): pode ser endossado ("X, da Hologram") sem depender dela.
5. **Domínio `.com.br` disponível** e **sem registro conflitante no INPI** na classe de software
   e serviços financeiros. Nenhuma das sugestões abaixo foi conferida: **disponibilidade de
   domínio e de marca a confirmar**.
6. "Olo Finance" foi sugerido e não pegou.

### 2.2 Sugestões (disponibilidade de domínio e de marca a confirmar em todas)

| Nome                    | Por quê                                                                                                                                    |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **Crivo**               | É o que a plataforma faz para o contador: o dado passa pelo crivo antes de entrar na contabilidade. Curto, português, verbo e substantivo. |
| **Lastro**              | Lastro contábil: cada lançamento com evidência por trás. Soa sólido e serve para financeiro e contábil.                                    |
| **Conferi**             | Nasce da frase do hero ("conferido antes de virar contabilidade"). Informal, lembra ação feita. Risco: parece nome de app de consumo.      |
| **Prumo**               | Instrumento que diz se está alinhado; "no prumo" é expressão comum. Serve para todo o produto, não só para conciliação.                    |
| **Partida**             | Partida dobrada, a linguagem do contador (a Sprint 16 deriva a partida do sinal). Risco: palavra genérica, busca difícil.                  |
| **Razão**               | Livro razão, e também "ter razão". Forte para contador; genérico demais para busca e marca.                                                |
| **Fecho**               | Fechamento do mês. Curto e direto; risco de soar só como "fechamento", sem a parte de categorização.                                       |
| **Aferi**               | Aferir: medir com padrão. Técnico, preciso, pouco usado como marca.                                                                        |
| **Baliza**              | Referência que orienta. Neutro, serve para BPO e contador.                                                                                 |
| **Esteira**             | O caminho do arquivo ao lançamento, passo a passo. Bom para descrever fluxo; fraco como marca sozinha.                                     |
| **Norte**               | Direção. Muito usado em marcas brasileiras; busca e registro provavelmente difíceis.                                                       |
| **Hologram Fechamento** | Marca endossada, zero risco de registro novo, comunica a função. Perde a independência da Hologram como organização.                       |

Recomendação para a conversa (não é decisão): **Crivo** ou **Lastro**, pelos critérios 1 a 3,
e conferir domínio e INPI dos dois antes de levar ao Lucas.

---

## 3. Roteiro depois da decisão

1. Conferir e **registrar o domínio** (`registro.br` para `.com.br`), no nome da Hologram.
2. **Reservar o IP global** e montar o Load Balancer HTTPS externo com serverless NEG para o
   serviço web do ambiente (seção 1.1). Começar por **dev**.
3. Criar o **certificado gerenciado** para o domínio e esperar o status ativo.
4. Apontar o **DNS** (`A` para o IP do Load Balancer) e esperar a propagação.
5. Atualizar as **env vars da API** sem deploy novo: `gcloud run services update <api> --update-env-vars=FRONTEND_URL=https://<domínio>,ALLOWED_ORIGINS=https://<domínio>` (o `ALLOWED_ORIGINS` é CSV: manter a URL `*.run.app` do web junto durante a transição).
6. **Testar o cookie e o login** pelo domínio novo: entrar, navegar, sair; conferir no
   DevTools que o cookie `access_token` está no host novo, HttpOnly e Secure.
7. Definir `metadataBase` na landing e conferir o `robots.txt` e o Open Graph pelo domínio novo.
8. Trocar o nome em `apps/web/src/lib/brand.ts` e `apps/api/app/core/branding.py` (subtask
   86e3fr9x3): hoje é uma linha de cada lado, e os testes `brand.test.ts` e `test_branding.py`
   recusam o nome antigo escrito em qualquer outro lugar.
9. Repetir 2 a 7 em **prod** quando o ambiente existir.

# Subprocessadores do Hologram OS

Terceiros que recebem dado do cliente final ou de usuário da plataforma, o que vai para
cada um e com que fonte. Escrito para responder pergunta de cliente sem depender de
memória (task ClickUp 86e3anx75, épico 86e3anwzu).

- **Conferido em:** 05/10/2026, contra a `origin/develop` em `81f0bde`.
- **Regra deste arquivo:** toda afirmação sobre o código aponta o arquivo que a prova, e
  toda afirmação sobre um terceiro aponta a página oficial dele com a data de acesso. O
  que depende de configuração de conta que não aparece no repositório fica marcado
  **a confirmar**, com quem confirma.
- **Quando atualizar:** entrou chamada nova a um serviço externo, mudou o que uma chamada
  envia, ou a página de termos de um terceiro mudou. Atualize a data de conferência.

## Resumo

| Terceiro | Papel | O que recebe | Onde roda |
| --- | --- | --- | --- |
| Anthropic | Leitura do arquivo e análise de classificação por IA | Arquivo da conciliação inteiro; descrição, valor, fornecedor e categoria de movimentações conciliadas; glossário do cliente | Fora do Brasil (ver seção) |
| Google Cloud | Hospedagem: aplicação, banco, chaves, imagens, segredos | Tudo que a plataforma guarda e processa | `southamerica-east1` (São Paulo), banco **a confirmar** |
| Omie | ERP do próprio cliente, leitura e escrita | Credencial do cliente; na escrita, a compra do cartão | Infraestrutura da Omie |
| Slack | Aviso de lead da landing e alertas de plantão | Dados do formulário público; alertas só com IDs | Infraestrutura do Slack |

## Anthropic

**O que é enviado.** Três chamadas à API de mensagens, todas pelo SDK oficial e pela
mesma chave (`ANTHROPIC_API_KEY`):

1. **Extração das movimentações** (`extract_movements`,
   `apps/api/app/integrations/anthropic/client.py:200`). O arquivo enviado na conciliação
   vai INTEIRO: PDF como bloco `document` em base64, qualquer outro formato como texto
   decodificado (`_build_user_content` e `_document_block`, mesmo arquivo, linhas 409 a
   455). Junto vai a instrução de extração e, quando houver, o banco e o tipo de conta
   identificados.
2. **Identificação do documento** (`identify_document`, mesmo arquivo, linha 287): em PDF
   dividido por páginas, a primeira página vai sozinha para identificar banco e tipo de
   conta.
3. **Análise de classificação** (`apps/api/app/modules/reconciliations/qualification/semantic.py`).
   Em lotes de até 50 pares, para cada movimentação conciliada: descrição do extrato,
   fornecedor e categoria lançados no ERP e valor (`_build_user_payload`, linha 473). No
   prompt de sistema vai o glossário do cliente já decifrado: código, nome e descrição de
   cada entrada (`render_glossary_block` e `_render_entry`, linhas 227 e 262). Liga e
   desliga por `QUALIFICATION_ENABLED` (`apps/api/app/core/config.py:202`).

**O que não é enviado.** Nome do cliente, CNPJ, credenciais, dados de usuário e a planilha
da origem por arquivo (Sprint 14), que é lida pelo mapeamento de colunas: o cliente da
Anthropic só é usado dentro de `modules/reconciliations/` (o `main.py` importa apenas a
validação de limites do modelo, sem chamada). O
cruzamento com o ERP é código determinístico, sem IA (CLAUDE.md §5.6).

**Para quê.** Transformar o arquivo do banco em linhas estruturadas, e apontar
classificação suspeita para uma pessoa revisar. A IA não decide match nem classificação.

**Modelo.** O padrão do código é `claude-sonnet-4-5`, com `claude-opus-4-6` como
alternativa (`apps/api/app/core/config.py:122-123`). O pipeline de deploy não sobrescreve
essas variáveis, mas uma variável de ambiente no Cloud Run sobrescreveria:
**a confirmar** no serviço, se algum dia alguém mudou o modelo à mão.

**Termos, conferidos em 05/10/2026:**

- **Treinamento.** Os termos comerciais dizem: "Anthropic may not train models on
  Customer Content from Services." Fonte:
  [Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms),
  vigente desde 17/06/2025. A página de retenção da API repete: "Retained data is never
  used for model training without your express permission." Fonte:
  [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention).
- **Retenção padrão.** "For Anthropic API users, we automatically delete inputs and
  outputs on our backend within 30 days of receipt or generation", com exceções: serviço
  com retenção controlada pelo cliente (por exemplo Files API, que a plataforma não usa),
  acordo de retenção zero, conteúdo sinalizado pelos sistemas automáticos de segurança
  como violação da política de uso (entradas e saídas por até 2 anos, notas de
  classificação por até 7), exigência legal, feedback enviado (5 anos) e pesquisa com
  dado anonimizado se o contrato permitir. Fonte:
  [How long do you store my organization's data?](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data),
  atualizada em 01/07/2026.
- **Retenção zero (ZDR).** Existe, por organização, a pedido ao time comercial da
  Anthropic: "Under a ZDR arrangement, Anthropic does not store customer prompts or
  responses at rest after the API response is returned." Os recursos que a plataforma
  usa (Messages API, PDF inline e prompt caching) constam como elegíveis. Os modelos que
  a plataforma usa não estão entre os que exigem retenção de 30 dias (Fable 5 e 5.1,
  Mythos 5 e 5.1). Fonte:
  [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention).
- **Prompt caching.** A plataforma marca o prompt de sistema e o glossário com
  `cache_control: ephemeral` (`client.py:405`, `semantic.py:392` e `semantic.py:416`).
  Pela mesma página, o cache guarda só representações internas e hashes, em memória,
  pelo tempo de vida do cache, e não o texto. Detalhes em
  [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
- **Onde roda.** Dado em repouso: "Currently, `"us"` is the only available workspace
  geo." Inferência: o padrão é `"global"`, que pode rodar em qualquer geografia; a única
  restrição oferecida é `"us"`, e o parâmetro só existe nos modelos 4.6 em diante (o
  `claude-sonnet-4-5` responde 400 se recebê-lo). O código não passa `inference_geo`.
  Não há região na América do Sul. Fonte:
  [Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency).
  Para a LGPD, isso é transferência internacional de dados.
- **DPA e subprocessadores dela.** Os termos comerciais incorporam o
  [Data Processing Addendum](https://www.anthropic.com/legal/data-processing-addendum)
  (vigente desde 24/02/2025), que usa as cláusulas-padrão da União Europeia para
  transferência internacional e não menciona LGPD. A lista de subprocessadores da
  Anthropic fica em [anthropic.com/subprocessors](https://www.anthropic.com/subprocessors),
  com aviso prévio de inclusão e prazo de 15 dias para objeção. No fim do contrato, a
  Anthropic devolve ou apaga os dados em até 30 dias.

**A confirmar pelo Pedro no console da Anthropic** (nada disso aparece no repositório):

- se a organização da Hologram tem retenção zero contratada (sem ela, vale a regra dos
  30 dias acima);
- qual o `default_inference_geo` e o `allowed_inference_geos` do workspace da chave em uso;
- se a conta está de fato sob os termos comerciais (chave de API de organização comercial,
  não plano de consumidor) e se o DPA foi aceito.

## Google Cloud

**O que é.** A hospedagem inteira, no projeto `liberdade-assessoria`:

- **Cloud Run** para a API, o web e os jobs (migração, limpeza, sincronização diária da
  carteira): `REGION: southamerica-east1` em `.github/workflows/deploy-dev.yml:58`.
- **Artifact Registry** para as imagens: `southamerica-east1-docker.pkg.dev`
  (`deploy-dev.yml:60`), com build pelo Cloud Build (`deploy-dev.yml:153`).
- **Cloud KMS** para a chave-mestra que embrulha a chave de cada cliente: keyring criado
  em `REGION`, padrão `southamerica-east1` (`scripts/setup-gcp.sh:49` e `:64`).
- **Secret Manager** para os segredos de runtime (`setup-gcp.sh:96-101` e `:172-190`).
- **Cloud Scheduler** para o job diário da carteira (`setup-gcp.sh:402-409`).
- **Cloud SQL** para o PostgreSQL: a conta de serviço recebe `roles/cloudsql.client`
  (`setup-gcp.sh:360-368`). A instância não é criada por nenhum script do repositório, então
  **a região do banco e dos backups está a confirmar** no console do GCP
  (`gcloud sql instances list --project liberdade-assessoria`).
- **Cloud Logging**, implicitamente: a API escreve log estruturado em stdout
  (`apps/api/app/core/logging.py:199`), e o Cloud Run guarda o stdout. O redactor do
  structlog mascara chave sensível, e a regra da casa proíbe logar conteúdo de arquivo,
  descrição, nome e credencial (CLAUDE.md §3.3).

**O que recebe.** Tudo que a plataforma guarda e processa, com os campos sensíveis cifrados
pela própria aplicação antes de chegar ao banco (ver "O que não sai da plataforma").

**Fontes, conferidas em 05/10/2026:**
[Cloud Data Processing Addendum](https://cloud.google.com/terms/data-processing-addendum),
[subprocessadores do Google Cloud](https://cloud.google.com/terms/subprocessors),
[regiões do Cloud Run](https://cloud.google.com/run/docs/locations).

## Omie

**O que é.** O ERP do próprio cliente. A plataforma fala com ele com a credencial que o
cliente fornece, guardada cifrada (`client_connections.credentials_encrypted`).

**O que vai para lá.** Na leitura (extrato, contas, categorias, títulos, cadastro de
fornecedor), só a credencial e os filtros da consulta. Na escrita, que só existe para
fatura de cartão e nasce desligada (`OMIE_POSTING_ENABLED`, CLAUDE.md §3.16), vão conta,
data, valor, categoria e a descrição da compra no campo de observação
(`apps/api/app/modules/reconciliations/omie_posting/service.py:30`).

**Por que é diferente dos outros.** Tudo que vai para a Omie já é dado do próprio cliente,
no sistema que ele mesmo contratou. A Omie é escolha e contrato do cliente, não da
Hologram. A plataforma não leva para lá dado de outro cliente nem dado que o cliente não
tenha.

**Fontes, conferidas em 05/10/2026:**
[Termos de uso](https://app.omie.com.br/termos-de-contrato/),
[Política de privacidade do Portal Omie](https://www.omie.com.br/uploads/politica-de-privacidade-omie-solucao.pdf),
[Segurança e privacidade](https://www.omie.com.br/seguranca-e-privacidade).

## Slack

**O que é.** Incoming webhooks, por duas rotas diferentes:

- **Aviso de lead da landing.** `build_slack_payload`
  (`apps/api/app/modules/leads/notifier.py:75`) leva nome, e-mail, empresa, WhatsApp, a
  mensagem (truncada) e a hora de quem preencheu o formulário público. É prospect, não
  cliente final (CLAUDE.md §4.5), mas é dado pessoal, e por isso o Slack está nesta lista.
  Secret `LEADS_SLACK_WEBHOOK_URL`.
- **Alertas de plantão.** `Alert` (`apps/api/app/core/alerting.py:94`) carrega só código do
  alerta, mensagem da aplicação e IDs de sessão e de cliente, nunca nome, descrição ou
  conteúdo de arquivo. O alerta pode ir também por e-mail SMTP, com o mesmo conteúdo; o
  servidor SMTP é configuração de ambiente (`ALERT_SMTP_HOST`), **a confirmar**.

**Fontes, conferidas em 05/10/2026:**
[Política de privacidade do Slack](https://slack.com/trust/privacy/privacy-policy),
[Data Processing Addendum do Slack](https://slack.com/terms-of-service/data-processing).

## O que não está ligado

O primer cita Sentry e Grafana/Loki na stack (CLAUDE.md §2), e nenhum dos dois recebe dado
hoje:

- **Sentry.** `sentry-sdk[fastapi]` é dependência (`apps/api/pyproject.toml:55`) e existe
  `SENTRY_DSN` em `apps/api/app/core/config.py:271`, mas nenhum arquivo chama
  `sentry_sdk.init`, e nem o pipeline nem o script de setup definem o DSN. O web não tem
  SDK do Sentry.
- **Grafana/Loki.** Aparecem só em comentário. Não há envio de log para eles; o log vai
  para stdout e fica no Cloud Logging.

Se algum dos dois for ligado, ele entra nesta lista antes do deploy.

## O que não sai da plataforma

O que já é diferencial de LGPD e existe no código:

- **O arquivo original nunca é guardado.** O upload é lido em memória e descartado. A
  linha do arquivo guarda só o SHA-256 do conteúdo e o nome cifrado
  (`apps/api/app/db/models/reconciliation_file.py:81-86`), e a única coluna binária de
  todo o modelo de dados é a chave embrulhada do cliente
  (`apps/api/app/db/models/client.py:80`). A exceção é o envio para a Anthropic descrito
  acima.
- **Nome de fornecedor e de categoria não ficam em claro.** O snapshot do ERP guarda só os
  códigos (`apps/api/app/db/models/reconciliation_omie_entry.py:72-73`); o nome é buscado na
  origem quando a tela precisa (CLAUDE.md §4.5).
- **Chave de cifra por cliente.** Cada cliente tem a própria chave (DEK), guardada
  embrulhada por uma chave-mestra no Cloud KMS, e o conteúdo cifrado de um cliente não
  decifra no contexto de outro. A lista dos 17 campos cifrados é o bloco de constantes de
  AAD de `apps/api/app/core/crypto_service.py:29-67` (CLAUDE.md §4.1).
- **Isolamento entre clientes e entre organizações.** Uma decisão de acesso única,
  `resolve_client_access` em `apps/api/app/core/authz.py`, aplicada na rota e no `WHERE`;
  116 endpoints sensíveis testados contra três atacantes
  (`apps/api/app/core/sensitive_endpoints.py`, CLAUDE.md §3.15).
- **Trilha de acesso só com IDs.** `access_audit` guarda usuário, cliente, sessão, ação,
  rota e hora, nunca nome ou conteúdo (`apps/api/app/db/models/access_audit.py:59-87`,
  CLAUDE.md §4.7).
- **Encerramento com apagamento criptográfico.** Encerrar um cliente apaga a chave dele
  (`client.dek_wrapped = None`, `apps/api/app/modules/clients/service.py:684`), o que torna
  ilegível de uma vez todo o conteúdo cifrado, e purga glossário, conexões e configuração
  (`close_client_purge`, `apps/api/app/modules/clients/repository.py:593`). A exclusão
  definitiva, para o apagamento pedido pelo titular, segue na API
  (`DELETE /clients/{id}`, `apps/api/app/modules/clients/routes.py:474`), sem botão na tela
  (CLAUDE.md §4.12).

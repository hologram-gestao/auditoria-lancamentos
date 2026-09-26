# Consumidores de origem e de cifra — Sprint 9 (BACK 09.6)

> **GERADO** por `scripts/gen_origin_consumers_inventory.py`. Não editar à mão:
> `tests/unit/test_origin_consumers_inventory.py` regenera e compara.

Símbolos rastreados: `build_omie_client` · `load_client_cipher` · `provision_client_cipher` · `build_capable_client` · `build_origin_client` · `client_from_credentials` · `resolve_capable_connection` · `resolve_origin_connections`

## Por que duas famílias diferentes

`load_client_cipher`/`provision_client_cipher` aparecem em dois papéis que
**não podem ser confundidos**: decifrar a CREDENCIAL de uma origem (vira
dependência de conexão) e cifrar DADO DO TENANT — nome de arquivo, descrição,
nota do analista, glossário (não vira). Converter o segundo em dependência de
origem faria o glossário de um cliente sem Omie parar de funcionar.

| Arquivo | Família | Símbolos | Nota |
| --- | --- | --- | --- |
| `app/core/crypto_service.py` | definição | `load_client_cipher` · `provision_client_cipher` | declara `load_client_cipher` e `provision_client_cipher` |
| `app/integrations/omie/lancamento_cache.py` | definição | `build_origin_client` | docstring: recebe o client já construído pelo caller, não o constrói |
| `app/integrations/omie/mock_client.py` | definição | `build_origin_client` | docstring do cliente-demo; a resolução mora no adaptador (09.2) |
| `app/modules/client_chart_of_accounts/service.py` | origem — convertido para o contrato | `build_origin_client` · `resolve_capable_connection` | sincroniza o plano de contas (S10): resolve a conexão capaz e constrói o client pela PORTA, sem tocar em credencial nem nas colunas antigas |
| `app/modules/client_connections/legacy_fallback.py` | fallback / conversão do R2 | `load_client_cipher` · `resolve_origin_connections` | único leitor das colunas antigas; sintetiza a conexão da janela (09.5) |
| `app/modules/client_connections/origin.py` | origem — convertido para o contrato | `build_omie_client` · `load_client_cipher` · `build_capable_client` · `build_origin_client` · `client_from_credentials` · `resolve_capable_connection` · `resolve_origin_connections` | a PORTA: resolve conexão capaz + decifra a credencial dela |
| `app/modules/client_connections/service.py` | origem — convertido para o contrato | `load_client_cipher` · `provision_client_cipher` · `resolve_origin_connections` | CRUD de conexão: cifra e decifra a credencial DA CONEXÃO |
| `app/modules/client_movements/service.py` | origem — convertido para o contrato | `resolve_capable_connection` | ingestão da base de movimentos por competência (S12, R0): resolve a conexão capaz de `listar_lancamentos` e constrói o provedor pela PORTA, iterando as contas do cache sob o lock por cliente. Não toca em credencial nem nas colunas antigas, e não fala com o `OmieClient` direto |
| `app/modules/client_titles/routes.py` | origem — convertido para o contrato | `build_capable_client` | lista da carteira (S11): resolve a conexão capaz de `listar_lancamentos` só para resolver NOME de devedor em runtime (§4.5), e é FAIL-SOFT — sem origem alcançável a lista sai com o código e `supplierNameResolved=false`, nunca 409 numa leitura |
| `app/modules/client_titles/service.py` | origem — convertido para o contrato | `load_client_cipher` · `provision_client_cipher` · `resolve_capable_connection` | ingestão da carteira de títulos (S11): resolve a conexão capaz de `listar_titulos_em_aberto` e constrói o provedor pela PORTA. Não toca em credencial nem nas colunas antigas, e não fala com o `OmieClient` direto. `TitleContextService` (S15) também mora aqui e usa `load_client_cipher`/`provision_client_cipher` para cifrar o TEXTO do contexto do título — dado do tenant, sem relação com origem |
| `app/modules/clients/accounts_cache.py` | origem — convertido para o contrato | `build_origin_client` | sync de contas por CONEXÃO (TTL em `client_connections.accounts_synced_at`) |
| `app/modules/clients/repository.py` | fallback / conversão do R2 | `resolve_origin_connections` | projeta o predicado do cliente legado em `WHERE` (`legacy_origin_available`) para derivar `origin_status` sem N+1 — a MESMA decisão de `resolve_origin_connections`, em SQL. Não constrói client de provedor nem lê credencial |
| `app/modules/clients/service.py` | origem — convertido para o contrato | `resolve_origin_connections` | detalhe (200 sempre) e sync manual (409 acionável); crypto-shredding do encerramento |
| `app/modules/glossary/service.py` | cifra de dado do tenant — declarado, sem conversão | `load_client_cipher` · `provision_client_cipher` | cifra ENTRADAS DO GLOSSÁRIO do tenant — nada a ver com origem. Verificado em 22/09: nenhuma chamada ao provedor. NÃO converter |
| `app/modules/omie_data/routes.py` | origem — convertido para o contrato | `build_capable_client` | categorias e lançamentos: resolve conexão capaz de `listar_lancamentos` |
| `app/modules/reconciliations/export/routes.py` | origem — convertido para o contrato | `build_capable_client` | enriquecimento de nomes em runtime: resolve conexão capaz |
| `app/modules/reconciliations/export/service.py` | cifra de dado do tenant — declarado, sem conversão | `load_client_cipher` | decifra descrição/nota para a planilha — dado do tenant |
| `app/modules/reconciliations/processing/job.py` | origem — convertido para o contrato | `provision_client_cipher` · `client_from_credentials` · `resolve_capable_connection` | resolve a conexão DENTRO da sessão e carrega a credencial; auth recusada marca a conexão em erro. O cipher aqui também cifra descrição (dado) |
| `app/modules/reconciliations/review/routes.py` | origem — convertido para o contrato | `build_capable_client` | lançamentos disponíveis e aba de divergências (esta com fail-soft anterior à sprint) |
| `app/modules/reconciliations/review/service.py` | cifra de dado do tenant — declarado, sem conversão | `load_client_cipher` · `provision_client_cipher` | cifra/decifra nota do analista e contexto de anomalia — dado do tenant |
| `app/modules/reconciliations/routes.py` | origem — convertido para o contrato | `load_client_cipher` · `provision_client_cipher` · `build_origin_client` · `resolve_capable_connection` | criação exige `listar_lancamentos` ANTES de gravar; lançamento exige `escrever`. Os demais usos do cipher aqui cifram descrição de linha (cifra de dado) |
| `app/modules/reconciliations/service.py` | cifra de dado do tenant — declarado, sem conversão | `load_client_cipher` · `provision_client_cipher` | cifra nome de arquivo e descrição das linhas — dado do tenant |

## Contagens

- **origem — convertido para o contrato**: 13
- **cifra de dado do tenant — declarado, sem conversão**: 4
- **definição**: 3
- **fallback / conversão do R2**: 2

## Invariante

**Zero** call sites de construção de client do provedor fora de
`modules/client_connections/origin.py` (que delega ao adaptador da 09.2).
O antigo `modules/clients/omie_factory.py` foi REMOVIDO nesta task — ficou
sem nenhum chamador.

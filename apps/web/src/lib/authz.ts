/**
 * Gating de UI — **um** helper, espelhando a matriz do backend (Sprint 5 / R4 +
 * camada de organizações, épico 86e36ec0q).
 *
 * Regra do learning "decisão derivada num só lugar": a pergunta "este usuário
 * pode ver/fazer X?" é respondida AQUI e em nenhum outro lugar. Proibido
 * `if (user.role === 'admin')` espalhado por componente — quando a matriz muda,
 * um `if` esquecido num canto vira ação visível que o servidor nega.
 *
 * **Isto não é segurança.** A autoridade é o backend
 * (`apps/api/app/core/authz.py` — `PERMISSION_MATRIX` + `resolve_client_access`),
 * que decide pela LINHA do usuário a cada request. O middleware do Next também
 * não é barreira (bypass por header — CVE-2025-29927). O que este módulo evita é
 * o defeito de UX de mostrar um botão que devolve 403.
 *
 * O acoplamento com o backend é o `UserRole` do **contrato gerado**: como a
 * matriz é um `Record<UserRole, ...>`, um papel novo no backend quebra a
 * compilação aqui até alguém decidir o que ele enxerga. Negado por padrão —
 * foi exatamente assim que `platform_admin` entrou (86e36ecwa).
 */
import type { AuthenticatedUser, UserRole } from '@/lib/contracts';

/**
 * Ações da matriz do PRD §4 com a camada de organizações. Os nomes são os
 * mesmos do enum `Permission` do backend (`app/core/authz.py`) — não são campos
 * de contrato (nenhum endpoint os expõe), então vivem aqui como vocabulário
 * compartilhado por convenção.
 */
export type Permission =
  | 'run_reconciliation'
  | 'review_export'
  | 'sync_omie_accounts'
  | 'manage_client_users'
  /**
   * Sprint 6 (BACK 06.3): manter o GLOSSÁRIO do tenant. Só a ESCRITA pede
   * permissão — a leitura é de todo papel com acesso ao cliente, porque o
   * operador usa o glossário como referência na revisão. Não existe permissão
   * de "ler glossário": inventá-la aqui criaria uma regra que o backend não
   * tem, e a tela negaria o que o servidor libera.
   */
  | 'manage_glossary'
  | 'edit_client'
  | 'view_other_tenant'
  /** Criar cliente (86e36ecjp): staff. O gerente que cria vira o responsável. */
  | 'create_client'
  /** Gerir o staff da organização (`/configuracoes/usuarios`). */
  | 'manage_org_users'
  /** Catálogo de categorias de cliente, por organização. */
  | 'manage_client_categories'
  /** Taxonomia global de tipos de anomalia (escrita). */
  | 'manage_anomaly_types'
  /** Administrar ORGANIZAÇÕES — só a plataforma. */
  | 'manage_platform'
  /** Disparo do alerta sintético (diagnóstico do plantão). */
  | 'run_alert_test'
  /**
   * Sprint 9 (R5): conectar, testar, alterar e remover ORIGENS de dado do
   * cliente (`client_connections`). Permissão PRÓPRIA, e não `edit_client`:
   * o `manager` cria cliente (✅) mas não edita (❌) — pendurar conexão em
   * `edit_client` faria o contador do escritório parceiro cadastrar a carteira
   * inteira e não conseguir conectar nenhuma delas.
   *
   * A LEITURA do estado da origem não pede permissão nenhuma (o backend libera
   * o `GET` a todo papel com acesso ao cliente): inventar uma aqui esconderia
   * do operador o motivo pelo qual a conciliação dele não roda.
   */
  | 'manage_client_connections'
  /**
   * Sprint 10 (R4): LER o plano de contas do cliente — lista e cobertura. Todo
   * papel com acesso ao tenant, o operador inclusive: é a classificação
   * contábil do próprio cliente e é o que explica o que a conciliação mostra.
   *
   * Ao contrário do glossário — onde a leitura NÃO tem permissão própria —
   * aqui ela tem, porque o backend a declara (`VIEW_CLIENT_CHART_OF_ACCOUNTS`,
   * com `ViewClientChartOfAccountsDep` na rota). Espelhar a permissão que
   * existe é a regra; inventar ou omitir uma é que cria divergência.
   */
  | 'view_client_chart_of_accounts'
  /**
   * Sprint 10 (R4): SINCRONIZAR o plano de contas (ir à origem). Permissão
   * PRÓPRIA, decidida no PRD, porque as duas reutilizações plausíveis dão
   * resultados OPOSTOS: `manage_client_categories` é admin-only e deixaria de
   * fora o `manager` do escritório parceiro — quem cadastra e conecta a
   * carteira; e `sync_omie_accounts` é de todos, o que deixaria o
   * `client_operator` forçar chamadas à origem do cliente.
   */
  | 'sync_client_chart_of_accounts'
  /**
   * Sprint 11 (R5): LER a CARTEIRA de títulos em aberto — lista e agregados.
   * Todo papel com acesso ao tenant, o operador inclusive: é a posição
   * financeira do próprio cliente, e é ela que explica o que ele cobra e paga.
   *
   * ⚠️ **O nome é `*_receivables` por CONTRATO do PRD**, mesmo a tabela do
   * backend chamando-se `client_titles` e a carteira incluindo os títulos **a
   * pagar**. Renomear aqui para casar com a tabela quebraria o espelho da
   * matriz do backend — que é exatamente o que este módulo existe para manter.
   */
  | 'view_client_receivables'
  /**
   * Sprint 11 (R5): SINCRONIZAR a carteira (ir à origem). Permissão PRÓPRIA
   * pelo mesmo raciocínio da S10, e não uma reutilização de
   * `sync_client_chart_of_accounts`: são duas idas à origem diferentes, e
   * amarrá-las faria o dia em que uma célula mudasse arrastar a outra junto,
   * sem ninguém ter pedido.
   */
  | 'sync_client_receivables'
  /**
   * Sprint 15 (BACK 15.1): LER o contexto de um título (acordo de pagamento,
   * antecipação, nota a cancelar, cobrança suspensa, perda provável). Todo
   * papel com acesso ao tenant — mesma base de `view_client_receivables`: é
   * leitura sobre a posição financeira do próprio cliente.
   */
  | 'view_title_context'
  /**
   * Sprint 15 (BACK 15.1): REGISTRAR contexto num título. Permissão PRÓPRIA,
   * decidida no PRD (§3) com as MESMAS células de `sync_client_receivables` —
   * o `client_operator` lê mas não escreve. Não é reuso por coincidência: as
   * duas perguntas puderam ter respostas diferentes.
   */
  | 'manage_title_context'
  /**
   * Sprint 12 (R0 — BACK 12.2): SINCRONIZAR a base de movimentos de uma
   * competência (ir à origem). As MESMAS células de `sync_client_receivables`,
   * decididas no PRD — e permissão PRÓPRIA pelo motivo da S11: reusar amarraria
   * duas sincronizações diferentes a uma decisão só. LER o estado da base não
   * pede permissão (quem alcança o cliente lê).
   */
  | 'sync_client_movements'
  /**
   * Sprint 12 (R6 — BACK 12.4): EDITAR o de-para de um cliente — decisões,
   * herança, confirmação em lote, importação e materialização. Células
   * decididas no PRD, e não `edit_client` (admin-only): o `manager` do
   * escritório parceiro constrói a carteira e precisa classificá-la. O
   * `client_operator` VÊ a tela (a leitura não tem permissão própria) e não
   * edita.
   */
  | 'manage_client_mapping'
  /**
   * Sprint 12 (R1 — BACK 12.3): ESCREVER no catálogo de destinos e alvos da
   * ORGANIZAÇÃO — plataforma e admin. A LEITURA do catálogo não pede
   * permissão: quem escolhe alvo (o `client_manager` inclusive) precisa ler.
   * ⚠️ Células decididas pelo planejador do backend (ADR-074-BE), pendentes de
   * validação humana — espelhadas como estão.
   */
  | 'manage_mapping_catalog';

/**
 * A matriz, indexada por PAPEL (e não por permissão) de propósito: assim o
 * `Record<UserRole, ...>` obriga a lista a cobrir todo papel do contrato.
 *
 * Transcrita célula a célula de `apps/api/app/core/authz.py::PERMISSION_MATRIX`
 * (23 permissões × 5 papéis desde a Sprint 12) e travada em `__tests__/authz.test.ts`.
 *
 * | Ação                          | platform_admin | admin | manager | client_manager | client_operator |
 * | ----------------------------- | -------------- | ----- | ------- | -------------- | --------------- |
 * | Criar/rodar conciliação       | ✅             | ✅    | ✅      | ✅             | ✅              |
 * | Revisar / exportar            | ✅             | ✅    | ✅      | ✅             | ✅              |
 * | Sincronizar contas do Omie    | ✅             | ✅    | ✅      | ✅             | ✅              |
 * | Gerir usuários do cliente     | ✅             | ✅    | ✅ (carteira) | ✅       | ❌              |
 * | Manter o glossário            | ✅             | ✅    | ✅ (carteira) | ✅       | ❌              |
 * | Criar cliente                 | ✅             | ✅    | ✅      | ❌             | ❌              |
 * | Editar/excluir/encerrar       | ✅             | ✅    | ❌      | ❌             | ❌              |
 * | Ver outro tenant              | ✅             | ✅ (própria org) | ✅ (carteira) | ❌ | ❌         |
 * | Gerir usuários da org         | ✅             | ✅    | ❌      | ❌             | ❌              |
 * | Categorias de cliente         | ✅             | ✅    | ❌      | ❌             | ❌              |
 * | Tipos de anomalia             | ✅             | ❌    | ❌      | ❌             | ❌              |
 * | Gerir organizações            | ✅             | ❌    | ❌      | ❌             | ❌              |
 * | Teste de alerta               | ✅             | ✅    | ❌      | ❌             | ❌              |
 * | Conexões de origem (S9)       | ✅             | ✅    | ✅ (carteira) | ❌       | ❌              |
 * | Ver plano de contas (S10)     | ✅             | ✅    | ✅ (carteira) | ✅       | ✅              |
 * | Sincronizar plano de contas   | ✅             | ✅    | ✅ (carteira) | ✅       | ❌              |
 * | Ver a carteira (S11)          | ✅             | ✅    | ✅ (carteira) | ✅       | ✅              |
 * | Sincronizar a carteira (S11)  | ✅             | ✅    | ✅ (carteira) | ✅       | ❌              |
 * | Ver contexto do título (S15)  | ✅             | ✅    | ✅ (carteira) | ✅       | ✅              |
 * | Registrar contexto (S15)      | ✅             | ✅    | ✅ (carteira) | ✅       | ❌              |
 * | Sincronizar movimentos (S12)  | ✅             | ✅    | ✅ (carteira) | ✅       | ❌              |
 * | Editar o de-para (S12)        | ✅             | ✅    | ✅ (carteira) | ✅       | ❌              |
 * | Catálogo do de-para (S12)     | ✅             | ✅ (org) | ❌   | ❌             | ❌              |
 *
 * "(carteira)" e "(própria org)" **não são células**: são `resolve_client_access`
 * e os filtros de coleção, no servidor. A célula diz se o papel pode a AÇÃO.
 */
const PERMISSION_MATRIX: Record<UserRole, readonly Permission[]> = {
  // D1 revisada pelo Lucas (09/09/2026): a plataforma vê e faz tudo — é o
  // acesso de suporte. Está em TODA linha, `manage_platform` inclusive.
  platform_admin: [
    'run_reconciliation',
    'review_export',
    'sync_omie_accounts',
    'manage_client_users',
    'manage_glossary',
    'edit_client',
    'view_other_tenant',
    'create_client',
    'manage_org_users',
    'manage_client_categories',
    'manage_anomaly_types',
    'manage_platform',
    'run_alert_test',
    'manage_client_connections',
    'view_client_chart_of_accounts',
    'sync_client_chart_of_accounts',
    'view_client_receivables',
    'sync_client_receivables',
    'view_title_context',
    'manage_title_context',
    'sync_client_movements',
    'manage_client_mapping',
    'manage_mapping_catalog',
  ],
  // D3 final (86e36ed1d): `manage_anomaly_types` saiu daqui. A taxonomia de
  // anomalias é uma tabela GLOBAL do produto — o admin de uma organização
  // editaria o catálogo que as outras usam. Espelha `_PLATFORM_ONLY` no backend.
  admin: [
    'run_reconciliation',
    'review_export',
    'sync_omie_accounts',
    'manage_client_users',
    'manage_glossary',
    'edit_client',
    'view_other_tenant',
    'create_client',
    'manage_org_users',
    'manage_client_categories',
    'run_alert_test',
    'manage_client_connections',
    'view_client_chart_of_accounts',
    'sync_client_chart_of_accounts',
    'view_client_receivables',
    'sync_client_receivables',
    'view_title_context',
    'manage_title_context',
    'sync_client_movements',
    'manage_client_mapping',
    // S12: o catálogo de destinos/alvos é configuração da ORGANIZAÇÃO — escreve
    // quem a administra (o "(org)" é o filtro do servidor, não a célula).
    'manage_mapping_catalog',
  ],
  // O gerente da organização enxerga outros tenants apenas dentro da carteira —
  // quem sabe a carteira é o backend (`client_assignments`), ver `canAccessClient`.
  // Ele MANTÉM o glossário e, desde a D2 (86e36ecjp), GERE os usuários dos
  // clientes da carteira: é a linha da matriz do backend, conferida em
  // `app/core/authz.py` antes de espelhar aqui.
  manager: [
    'run_reconciliation',
    'review_export',
    'sync_omie_accounts',
    'manage_client_users',
    'manage_glossary',
    'view_other_tenant',
    'create_client',
    // S9 (R5): o gerente ENTRA. Ele cria o cliente e precisa conectar a origem
    // dele — o "(carteira)" é `resolve_client_access` no servidor, não a célula.
    'manage_client_connections',
    'view_client_chart_of_accounts',
    'sync_client_chart_of_accounts',
    'view_client_receivables',
    'sync_client_receivables',
    'view_title_context',
    'manage_title_context',
    'sync_client_movements',
    // S12 (R6): a armadilha que o PRD fechou — o contador parceiro (manager)
    // classifica a carteira que ele mesmo constrói. Sem catálogo: esse é da org.
    'manage_client_mapping',
  ],
  client_manager: [
    'run_reconciliation',
    'review_export',
    'sync_omie_accounts',
    'manage_client_users',
    'manage_glossary',
    'view_client_chart_of_accounts',
    'sync_client_chart_of_accounts',
    'view_client_receivables',
    'sync_client_receivables',
    'view_title_context',
    'manage_title_context',
    'sync_client_movements',
    'manage_client_mapping',
  ],
  // S10 (R4) e S11 (R5): o operador LÊ o plano de contas e a carteira, e **não**
  // sincroniza nenhum dos dois — são os únicos ❌ das duas linhas de
  // sincronizar. Sincronizar é uma ida à origem do cliente, e o operador é quem
  // mais abre tela.
  client_operator: [
    'run_reconciliation',
    'review_export',
    'sync_omie_accounts',
    'view_client_chart_of_accounts',
    'view_client_receivables',
    'view_title_context',
  ],
};

type MaybeUser =
  | Pick<
      AuthenticatedUser,
      'role' | 'scope' | 'client_id' | 'organization_id' | 'organization_name'
    >
  | null
  | undefined;

/**
 * Consulta a matriz. Negado por padrão: sem usuário, ou papel desconhecido, é
 * `false`. O cast para `| undefined` não é decorativo — `Record<UserRole, …>`
 * garante a chave em tempo de compilação, mas em runtime o `role` chega do
 * servidor e um valor fora do union cairia em `undefined`.
 */
export function hasPermission(user: MaybeUser, permission: Permission): boolean {
  if (!user) return false;
  const allowed = PERMISSION_MATRIX[user.role] as readonly Permission[] | undefined;
  return allowed?.includes(permission) ?? false;
}

/** `true` quando o usuário pertence a um tenant (usuário DO cliente). */
export function isClientScoped(user: MaybeUser): boolean {
  return user?.scope === 'client';
}

/** `true` para o staff de UMA organização (`admin`/`manager`). */
export function isSystemScoped(user: MaybeUser): boolean {
  return user?.scope === 'system';
}

/**
 * `true` para a administração geral da ADL — a plataforma **bem formada**:
 * escopo `platform` SEM organização e SEM tenant.
 *
 * Os dois `null` não são decorativos: espelham `CurrentUser.is_platform` do
 * backend (`authz.py`). Uma linha `platform` que carregue organização ou tenant
 * é corrompida (o CHECK do banco a recusa) e NÃO ganha alcance total — negado
 * por padrão, como lá.
 */
export function isPlatformScoped(user: MaybeUser): boolean {
  return (
    user?.scope === 'platform' &&
    (user.organization_id ?? null) === null &&
    (user.client_id ?? null) === null
  );
}

/**
 * Quem OPERA clientes: plataforma ou staff de organização — o oposto de
 * "é usuário de cliente". Espelha `CurrentUser.is_staff` do backend.
 */
export function isStaff(user: MaybeUser): boolean {
  return isPlatformScoped(user) || isSystemScoped(user);
}

/**
 * Espelha `resolve_client_access` (backend) **no que o front consegue saber**.
 *
 * - `scope='client'` → libera apenas o próprio `client_id`. É a decisão inteira:
 *   o front tem o dado necessário e pode degradar o deep link sem ida ao servidor.
 * - plataforma e staff de organização → devolve `true`. A organização do cliente
 *   e a carteira do `manager` moram no banco, que o front não conhece; quem nega
 *   é o backend (403/404) e a tela degrada pela resposta, não por adivinhação.
 */
export function canAccessClient(user: MaybeUser, targetClientId: string): boolean {
  if (!user) return false;
  if (isClientScoped(user)) {
    return user.client_id !== null && user.client_id === targetClientId;
  }
  return isStaff(user);
}

/**
 * A lista GLOBAL de clientes e as telas de `configuracoes/*` são território de
 * quem opera clientes — um usuário de tenant não tem o que fazer lá (e a rota
 * global sequer é escopável). O que cada um VÊ dentro de Configurações é
 * decidido item a item pela matriz, não por esta função.
 */
export function canSeeSystemArea(user: MaybeUser): boolean {
  return isStaff(user);
}

/**
 * Administra o staff da organização (`/configuracoes/usuarios`).
 *
 * Passou a consultar a matriz (`manage_org_users`) em vez de comparar o papel:
 * com a camada de organizações quem administra staff é a plataforma E o admin
 * da organização, e a lista de quem pode mora num lugar só.
 */
export function canManageSystemUsers(user: MaybeUser): boolean {
  return hasPermission(user, 'manage_org_users');
}

/**
 * Para onde o usuário volta quando cai numa rota que não pode ver.
 *
 * Usuário de tenant não tem "lista de clientes" para onde voltar — a casa dele
 * é o próprio cliente. Mandar todo mundo para `/clientes` daria um caminho de
 * volta que também é negado (dois becos sem saída em sequência). A plataforma
 * mora na mesma casa do staff: a lista de clientes, agora de todas as orgs.
 */
export function homePathFor(user: MaybeUser): string {
  if (isClientScoped(user) && user?.client_id) {
    return `/clientes/${user.client_id}`;
  }
  return '/clientes';
}

/** Rótulos PT-BR dos papéis — fonte única para header, tabelas e mensagens. */
export const USER_ROLE_LABELS: Record<UserRole, string> = {
  platform_admin: 'Administrador da plataforma',
  admin: 'Administrador',
  manager: 'Gerente',
  client_manager: 'Gerente do cliente',
  client_operator: 'Operador do cliente',
};

/** Nunca mostra o valor cru do enum ("Client_manager") na interface. */
export function roleLabel(user: MaybeUser): string {
  if (!user) return '';
  return (USER_ROLE_LABELS as Record<string, string | undefined>)[user.role] ?? user.role;
}

/**
 * Em que "chapéu" a pessoa está, para o header (86e36ecwa).
 *
 * Com N organizações, o papel sozinho deixou de dizer o contexto: "Administrador"
 * não distingue quem administra a Hologram de quem administra a Prospecta. A
 * plataforma não tem organização — o rótulo dela é o próprio escopo.
 * `null` quando não há o que dizer (linha sem organização fora da plataforma é
 * estado inválido; o header simplesmente omite em vez de mostrar "undefined").
 */
export function organizationLabel(user: MaybeUser): string | null {
  if (!user) return null;
  if (isPlatformScoped(user)) return 'Plataforma';
  return user.organization_name ?? null;
}

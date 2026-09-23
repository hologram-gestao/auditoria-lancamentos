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
  | 'manage_client_connections';

/**
 * A matriz, indexada por PAPEL (e não por permissão) de propósito: assim o
 * `Record<UserRole, ...>` obriga a lista a cobrir todo papel do contrato.
 *
 * Transcrita célula a célula de `apps/api/app/core/authz.py::PERMISSION_MATRIX`
 * (14 permissões × 5 papéis desde a Sprint 9) e travada em `__tests__/authz.test.ts`.
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
  ],
  client_manager: [
    'run_reconciliation',
    'review_export',
    'sync_omie_accounts',
    'manage_client_users',
    'manage_glossary',
  ],
  client_operator: ['run_reconciliation', 'review_export', 'sync_omie_accounts'],
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

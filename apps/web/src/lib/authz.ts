/**
 * Gating de UI — **um** helper, espelhando a matriz do backend (Sprint 5 / R4).
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
 * compilação aqui até alguém decidir o que ele enxerga. Negado por padrão.
 */
import type { AuthenticatedUser, UserRole } from '@/lib/contracts';

/**
 * Ações da matriz do PRD §4. Os nomes são os mesmos do enum `Permission` do
 * backend (`app/core/authz.py`) — não são campos de contrato (nenhum endpoint
 * os expõe), então vivem aqui como vocabulário compartilhado por convenção.
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
  | 'view_other_tenant';

/**
 * A matriz, indexada por PAPEL (e não por permissão) de propósito: assim o
 * `Record<UserRole, ...>` obriga a lista a cobrir todo papel do contrato.
 *
 * | Ação                          | client_manager | client_operator | admin | manager |
 * | ----------------------------- | -------------- | --------------- | ----- | ------- |
 * | Criar/rodar conciliação       | ✅             | ✅              | ✅    | ✅      |
 * | Revisar / exportar            | ✅             | ✅              | ✅    | ✅      |
 * | Sincronizar contas do Omie    | ✅             | ✅              | ✅    | ✅      |
 * | Gerir usuários do cliente     | ✅             | ❌              | ✅    | ❌      |
 * | Manter o glossário (S6)       | ✅             | ❌              | ✅    | ✅ (carteira) |
 * | Editar dados do cliente (§9)  | ❌             | ❌              | ✅    | ❌      |
 * | Ver outro tenant              | ❌             | ❌              | ✅    | ✅ (carteira) |
 */
const PERMISSION_MATRIX: Record<UserRole, readonly Permission[]> = {
  admin: [
    'run_reconciliation',
    'review_export',
    'sync_omie_accounts',
    'manage_client_users',
    'manage_glossary',
    'edit_client',
    'view_other_tenant',
  ],
  // O gerente do sistema enxerga outros tenants apenas dentro da carteira —
  // quem sabe a carteira é o backend (`client_assignments`), ver `canAccessClient`.
  // Ele MANTÉM o glossário (diferente de `manage_client_users`): é a linha da
  // matriz do backend, conferida em `app/core/authz.py` antes de espelhar aqui.
  manager: [
    'run_reconciliation',
    'review_export',
    'sync_omie_accounts',
    'manage_glossary',
    'view_other_tenant',
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

type MaybeUser = Pick<AuthenticatedUser, 'role' | 'scope' | 'client_id'> | null | undefined;

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

/** `true` para a equipe Hologram (`admin`/`manager` com `scope='system'`). */
export function isSystemScoped(user: MaybeUser): boolean {
  return user?.scope === 'system';
}

/**
 * Espelha `resolve_client_access` (backend) **no que o front consegue saber**.
 *
 * - `scope='client'` → libera apenas o próprio `client_id`. É a decisão inteira:
 *   o front tem o dado necessário e pode degradar o deep link sem ida ao servidor.
 * - `scope='system'` → devolve `true`. A carteira do `manager` mora em
 *   `client_assignments`, que o front não conhece; quem nega é o backend (403/404)
 *   e a tela degrada pela resposta, não por adivinhação.
 */
export function canAccessClient(user: MaybeUser, targetClientId: string): boolean {
  if (!user) return false;
  if (isClientScoped(user)) {
    return user.client_id !== null && user.client_id === targetClientId;
  }
  return isSystemScoped(user);
}

/**
 * A lista GLOBAL de clientes e as telas de `configuracoes/*` são território da
 * equipe Hologram — um usuário de tenant não tem o que fazer lá (e a rota
 * global sequer é escopável).
 */
export function canSeeSystemArea(user: MaybeUser): boolean {
  return isSystemScoped(user);
}

/** Apenas o admin do sistema administra usuários DO SISTEMA (Doc §8, admin-only). */
export function canManageSystemUsers(user: MaybeUser): boolean {
  return isSystemScoped(user) && user?.role === 'admin';
}

/**
 * Para onde o usuário volta quando cai numa rota que não pode ver.
 *
 * Usuário de tenant não tem "lista de clientes" para onde voltar — a casa dele
 * é o próprio cliente. Mandar todo mundo para `/clientes` daria um caminho de
 * volta que também é negado (dois becos sem saída em sequência).
 */
export function homePathFor(user: MaybeUser): string {
  if (isClientScoped(user) && user?.client_id) {
    return `/clientes/${user.client_id}`;
  }
  return '/clientes';
}

/** Rótulos PT-BR dos papéis — fonte única para header, tabelas e mensagens. */
export const USER_ROLE_LABELS: Record<UserRole, string> = {
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

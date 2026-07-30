/**
 * Matriz de permissões do front (FRONT 05.7 / R4).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * Cada célula da matriz do PRD §4 vira um caso — inclusive **toda célula `❌`**,
 * que é o que a task cobra ("cada célula ❌ tem caso negativo"). O espelho no
 * backend é `apps/api/app/core/authz.py::PERMISSION_MATRIX`; se um dos dois
 * mudar sozinho, é aqui que a divergência aparece.
 */
import { describe, expect, it } from 'vitest';

import {
  canAccessClient,
  canManageSystemUsers,
  canSeeSystemArea,
  hasPermission,
  homePathFor,
  isClientScoped,
  isSystemScoped,
  roleLabel,
  type Permission,
} from '@/lib/authz';
import type { AuthenticatedUser } from '@/lib/contracts';

const TENANT_A = '11111111-1111-4111-8111-111111111111';
const TENANT_B = '22222222-2222-4222-8222-222222222222';

const admin: AuthenticatedUser = {
  id: 'a',
  email: 'admin@hologram.com.br',
  name: 'Admin',
  role: 'admin',
  scope: 'system',
  client_id: null,
};
const manager: AuthenticatedUser = { ...admin, id: 'm', role: 'manager' };
const clientManager: AuthenticatedUser = {
  id: 'cm',
  email: 'gerente@cliente.com.br',
  name: 'Gerente do Cliente',
  role: 'client_manager',
  scope: 'client',
  client_id: TENANT_A,
};
const clientOperator: AuthenticatedUser = { ...clientManager, id: 'co', role: 'client_operator' };

/**
 * A tabela do PRD §4, transcrita. `true` = ✅, `false` = ❌ (caso negativo).
 */
const MATRIX: ReadonlyArray<{
  permission: Permission;
  admin: boolean;
  manager: boolean;
  clientManager: boolean;
  clientOperator: boolean;
}> = [
  {
    permission: 'run_reconciliation',
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: true,
  },
  {
    permission: 'review_export',
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: true,
  },
  {
    permission: 'sync_omie_accounts',
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: true,
  },
  {
    permission: 'manage_client_users',
    admin: true,
    manager: false,
    clientManager: true,
    clientOperator: false,
  },
  {
    permission: 'edit_client',
    admin: true,
    manager: false,
    clientManager: false,
    clientOperator: false,
  },
  {
    permission: 'view_other_tenant',
    admin: true,
    manager: true,
    clientManager: false,
    clientOperator: false,
  },
];

describe('hasPermission — matriz do PRD §4', () => {
  it.each(MATRIX)('$permission: admin=$admin', ({ permission, admin: expected }) => {
    expect(hasPermission(admin, permission)).toBe(expected);
  });

  it.each(MATRIX)('$permission: gerente do sistema=$manager', ({ permission, manager: e }) => {
    expect(hasPermission(manager, permission)).toBe(e);
  });

  it.each(MATRIX)('$permission: gerente do cliente=$clientManager', ({ permission, ...row }) => {
    expect(hasPermission(clientManager, permission)).toBe(row.clientManager);
  });

  it.each(MATRIX)('$permission: operador do cliente=$clientOperator', ({ permission, ...row }) => {
    expect(hasPermission(clientOperator, permission)).toBe(row.clientOperator);
  });

  it('nega por padrão: sem usuário e com papel desconhecido', () => {
    expect(hasPermission(null, 'run_reconciliation')).toBe(false);
    expect(hasPermission(undefined, 'run_reconciliation')).toBe(false);
    // Papel que o backend poderia introduzir sem o front saber. O cast existe
    // porque o contrato não conhece este valor — é exatamente o cenário.
    const futuro = { ...admin, role: 'auditor' } as unknown as AuthenticatedUser;
    expect(hasPermission(futuro, 'review_export')).toBe(false);
  });
});

describe('canAccessClient — isolamento de tenant na UI', () => {
  it('usuário de tenant acessa só o próprio cliente', () => {
    expect(canAccessClient(clientManager, TENANT_A)).toBe(true);
    expect(canAccessClient(clientOperator, TENANT_A)).toBe(true);
  });

  it('usuário de tenant NÃO acessa outro tenant (caso negativo)', () => {
    expect(canAccessClient(clientManager, TENANT_B)).toBe(false);
    expect(canAccessClient(clientOperator, TENANT_B)).toBe(false);
  });

  it('usuário de tenant sem client_id não acessa nada (estado inválido)', () => {
    const quebrado = { ...clientManager, client_id: null };
    expect(canAccessClient(quebrado, TENANT_A)).toBe(false);
  });

  it('equipe do sistema passa: a carteira é decidida pelo backend', () => {
    expect(canAccessClient(admin, TENANT_B)).toBe(true);
    expect(canAccessClient(manager, TENANT_B)).toBe(true);
  });

  it('sem usuário, nega', () => {
    expect(canAccessClient(null, TENANT_A)).toBe(false);
  });
});

describe('escopo, área do sistema e caminho de volta', () => {
  it('separa tenant de equipe Hologram', () => {
    expect(isClientScoped(clientOperator)).toBe(true);
    expect(isClientScoped(admin)).toBe(false);
    expect(isSystemScoped(admin)).toBe(true);
    expect(isSystemScoped(clientManager)).toBe(false);
  });

  it('área do sistema (lista global + configurações) é só da equipe Hologram', () => {
    expect(canSeeSystemArea(admin)).toBe(true);
    expect(canSeeSystemArea(manager)).toBe(true);
    expect(canSeeSystemArea(clientManager)).toBe(false);
    expect(canSeeSystemArea(clientOperator)).toBe(false);
  });

  it('gestão de usuários DO SISTEMA é só do admin', () => {
    expect(canManageSystemUsers(admin)).toBe(true);
    expect(canManageSystemUsers(manager)).toBe(false);
    // Gerente do cliente administra o TENANT dele, nunca a Hologram.
    expect(canManageSystemUsers(clientManager)).toBe(false);
  });

  it('o caminho de volta é a casa do papel — nunca um segundo beco sem saída', () => {
    expect(homePathFor(admin)).toBe('/clientes');
    expect(homePathFor(manager)).toBe('/clientes');
    expect(homePathFor(clientManager)).toBe(`/clientes/${TENANT_A}`);
    expect(homePathFor(null)).toBe('/clientes');
  });

  it('rótulo do papel é PT-BR, nunca o enum cru', () => {
    expect(roleLabel(clientManager)).toBe('Gerente do cliente');
    expect(roleLabel(clientOperator)).toBe('Operador do cliente');
    expect(roleLabel(admin)).toBe('Administrador');
    expect(roleLabel(manager)).toBe('Gerente');
  });
});

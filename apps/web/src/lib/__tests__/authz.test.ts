/**
 * Matriz de permissões do front (FRONT 05.7 / R4 + camada de organizações).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * Cada célula da matriz vira um caso — inclusive **toda célula `❌`**, que é o
 * que a task cobra. O espelho no backend é
 * `apps/api/app/core/authz.py::PERMISSION_MATRIX` (23 permissões × 5 papéis
 * desde a Sprint 12); se um dos dois mudar sozinho, é aqui que a divergência
 * aparece.
 */
import { describe, expect, it } from 'vitest';

import {
  canAccessClient,
  canManageSystemUsers,
  canSeeSystemArea,
  hasPermission,
  homePathFor,
  isClientScoped,
  isPlatformScoped,
  isStaff,
  isSystemScoped,
  organizationLabel,
  roleLabel,
  type Permission,
} from '@/lib/authz';
import type { AuthenticatedUser } from '@/lib/contracts';

const TENANT_A = '11111111-1111-4111-8111-111111111111';
const TENANT_B = '22222222-2222-4222-8222-222222222222';
const ORG_HOLOGRAM = '0706eeb5-9718-4d03-bcda-ef615789e6ac';
const ORG_PROSPECTA = '33333333-3333-4333-8333-333333333333';

/**
 * A plataforma é a única linha SEM organização e SEM tenant — é o que o CHECK
 * do banco exige e o que `is_platform` confere no backend.
 */
const platform: AuthenticatedUser = {
  id: 'p',
  email: 'plataforma@hologram.com.br',
  name: 'Plataforma',
  role: 'platform_admin',
  scope: 'platform',
  client_id: null,
  organization_id: null,
  organization_name: null,
};
const admin: AuthenticatedUser = {
  id: 'a',
  email: 'admin@hologram.com.br',
  name: 'Admin',
  role: 'admin',
  scope: 'system',
  client_id: null,
  organization_id: ORG_HOLOGRAM,
  organization_name: 'Hologram',
};
const manager: AuthenticatedUser = { ...admin, id: 'm', role: 'manager' };
const clientManager: AuthenticatedUser = {
  id: 'cm',
  email: 'gerente@cliente.com.br',
  name: 'Gerente do Cliente',
  role: 'client_manager',
  scope: 'client',
  client_id: TENANT_A,
  organization_id: ORG_HOLOGRAM,
  organization_name: 'Hologram',
};
const clientOperator: AuthenticatedUser = { ...clientManager, id: 'co', role: 'client_operator' };

/**
 * A matriz do backend, transcrita. `true` = ✅, `false` = ❌ (caso negativo).
 *
 * "(carteira)" e "(própria org)" NÃO aparecem aqui: não são células, são
 * `resolve_client_access` no servidor. A célula diz se o papel pode a AÇÃO.
 */
const MATRIX: ReadonlyArray<{
  permission: Permission;
  platform: boolean;
  admin: boolean;
  manager: boolean;
  clientManager: boolean;
  clientOperator: boolean;
}> = [
  {
    permission: 'run_reconciliation',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: true,
  },
  {
    permission: 'review_export',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: true,
  },
  {
    permission: 'sync_omie_accounts',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: true,
  },
  // D2 (86e36ecjp): o gerente da organização passou a gerir os usuários dos
  // clientes DA CARTEIRA — a célula abriu no backend e este espelho a segue.
  {
    permission: 'manage_client_users',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: false,
  },
  {
    permission: 'manage_glossary',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: false,
  },
  {
    permission: 'edit_client',
    platform: true,
    admin: true,
    manager: false,
    clientManager: false,
    clientOperator: false,
  },
  {
    permission: 'view_other_tenant',
    platform: true,
    admin: true,
    manager: true,
    clientManager: false,
    clientOperator: false,
  },
  {
    permission: 'create_client',
    platform: true,
    admin: true,
    manager: true,
    clientManager: false,
    clientOperator: false,
  },
  {
    permission: 'manage_org_users',
    platform: true,
    admin: true,
    manager: false,
    clientManager: false,
    clientOperator: false,
  },
  {
    permission: 'manage_client_categories',
    platform: true,
    admin: true,
    manager: false,
    clientManager: false,
    clientOperator: false,
  },
  // D3 final (86e36ed1d): taxonomia GLOBAL, escrita só da plataforma. O admin
  // saiu da célula na MESMA entrega em que a tela virou só-plataforma — tirar
  // antes teria deixado a tela com botões que o servidor nega.
  {
    permission: 'manage_anomaly_types',
    platform: true,
    admin: false,
    manager: false,
    clientManager: false,
    clientOperator: false,
  },
  {
    permission: 'manage_platform',
    platform: true,
    admin: false,
    manager: false,
    clientManager: false,
    clientOperator: false,
  },
  {
    permission: 'run_alert_test',
    platform: true,
    admin: true,
    manager: false,
    clientManager: false,
    clientOperator: false,
  },
  // S9 / R5: a única célula nova em que o `manager` ✅ e o `client_manager` ❌
  // — o inverso de `manage_glossary`. É de propósito: credencial de sistema
  // contábil é configuração do escritório, não do cliente final; e sem o
  // gerente aqui, quem cadastra a carteira não consegue conectá-la.
  {
    permission: 'manage_client_connections',
    platform: true,
    admin: true,
    manager: true,
    clientManager: false,
    clientOperator: false,
  },
  // S10 / R4: LER o plano de contas é de TODOS — é a classificação contábil do
  // próprio cliente, e o operador precisa dela para entender a conciliação.
  {
    permission: 'view_client_chart_of_accounts',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: true,
  },
  // S10 / R4: SINCRONIZAR sai do `client_operator` e **só dele** — a única
  // diferença entre as duas linhas novas, e a razão de serem duas permissões.
  // Nenhuma das existentes servia: `manage_client_categories` (admin-only)
  // excluiria o gerente que cadastra a carteira; `sync_omie_accounts` (todos)
  // deixaria o operador forçar chamadas à origem.
  {
    permission: 'sync_client_chart_of_accounts',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: false,
  },
  // S11 / R5: LER a carteira é de TODOS — é a posição financeira do próprio
  // cliente, e o operador precisa dela para entender o que cobra e o que paga.
  {
    permission: 'view_client_receivables',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: true,
  },
  // S11 / R5: SINCRONIZAR a carteira sai do `client_operator` e **só dele** — as
  // mesmas células do plano de contas, e não por preguiça: a pergunta é a mesma
  // ("quem faz o servidor ir à origem") e a resposta do PRD coincidiu. São duas
  // permissões distintas porque amarrá-las faria uma mudança de célula arrastar
  // a outra.
  {
    permission: 'sync_client_receivables',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: false,
  },
  // S15 / §3: LER o contexto do título é de TODOS — mesma base de
  // `view_client_receivables`.
  {
    permission: 'view_title_context',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: true,
  },
  // S15 / §3: REGISTRAR contexto sai do `client_operator` e **só dele** — as
  // mesmas células de `sync_client_receivables`, decisão própria do PRD.
  {
    permission: 'manage_title_context',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: false,
  },
  // S12 / R0: SINCRONIZAR a base de movimentos — as MESMAS células de
  // `sync_client_receivables` (PRD), em permissão própria.
  {
    permission: 'sync_client_movements',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: false,
  },
  // S12 / R6: as 5 células do PRD. O `manager` ENTRA — é a armadilha que o R6
  // fechou (com `edit_client` o parceiro montaria a carteira e não a classificaria).
  {
    permission: 'manage_client_mapping',
    platform: true,
    admin: true,
    manager: true,
    clientManager: true,
    clientOperator: false,
  },
  // S12 / R1: o catálogo é configuração da ORGANIZAÇÃO — plataforma e admin
  // (ADR-074-BE, decisão do planejador do backend).
  {
    permission: 'manage_mapping_catalog',
    platform: true,
    admin: true,
    manager: false,
    clientManager: false,
    clientOperator: false,
  },
];

describe('hasPermission — matriz do backend, célula a célula', () => {
  it.each(MATRIX)('$permission: plataforma=$platform', ({ permission, platform: expected }) => {
    expect(hasPermission(platform, permission)).toBe(expected);
  });

  it.each(MATRIX)('$permission: admin=$admin', ({ permission, admin: expected }) => {
    expect(hasPermission(admin, permission)).toBe(expected);
  });

  it.each(MATRIX)('$permission: gerente da organização=$manager', ({ permission, manager: e }) => {
    expect(hasPermission(manager, permission)).toBe(e);
  });

  it.each(MATRIX)('$permission: gerente do cliente=$clientManager', ({ permission, ...row }) => {
    expect(hasPermission(clientManager, permission)).toBe(row.clientManager);
  });

  it.each(MATRIX)('$permission: operador do cliente=$clientOperator', ({ permission, ...row }) => {
    expect(hasPermission(clientOperator, permission)).toBe(row.clientOperator);
  });

  it('a plataforma está em TODA linha (D1 revisada em 09/09/2026)', () => {
    // O mesmo invariante que o backend trava num teste unitário: permissão nova
    // sem a célula da plataforma é regressão do acesso de suporte.
    for (const row of MATRIX) {
      expect(hasPermission(platform, row.permission)).toBe(true);
    }
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

  it('staff passa: organização e carteira são decididas pelo backend', () => {
    expect(canAccessClient(admin, TENANT_B)).toBe(true);
    expect(canAccessClient(manager, TENANT_B)).toBe(true);
    // A plataforma entra em cliente de QUALQUER organização (acesso de suporte).
    expect(canAccessClient(platform, TENANT_A)).toBe(true);
    expect(canAccessClient(platform, TENANT_B)).toBe(true);
  });

  it('sem usuário, nega', () => {
    expect(canAccessClient(null, TENANT_A)).toBe(false);
  });
});

describe('escopo, área do sistema e caminho de volta', () => {
  it('separa os três escopos', () => {
    expect(isClientScoped(clientOperator)).toBe(true);
    expect(isClientScoped(admin)).toBe(false);
    expect(isSystemScoped(admin)).toBe(true);
    expect(isSystemScoped(platform)).toBe(false);
    expect(isPlatformScoped(platform)).toBe(true);
    expect(isPlatformScoped(admin)).toBe(false);
  });

  it('staff é quem OPERA clientes: plataforma ou organização', () => {
    expect(isStaff(platform)).toBe(true);
    expect(isStaff(admin)).toBe(true);
    expect(isStaff(manager)).toBe(true);
    expect(isStaff(clientManager)).toBe(false);
    expect(isStaff(clientOperator)).toBe(false);
    expect(isStaff(null)).toBe(false);
  });

  it('área do sistema (lista global + configurações) é de quem opera clientes', () => {
    expect(canSeeSystemArea(platform)).toBe(true);
    expect(canSeeSystemArea(admin)).toBe(true);
    expect(canSeeSystemArea(manager)).toBe(true);
    expect(canSeeSystemArea(clientManager)).toBe(false);
    expect(canSeeSystemArea(clientOperator)).toBe(false);
  });

  it('gestão de usuários da ORGANIZAÇÃO segue a matriz, não o papel', () => {
    expect(canManageSystemUsers(platform)).toBe(true);
    expect(canManageSystemUsers(admin)).toBe(true);
    expect(canManageSystemUsers(manager)).toBe(false);
    // Gerente do cliente administra o TENANT dele, nunca a organização.
    expect(canManageSystemUsers(clientManager)).toBe(false);
  });

  it('o caminho de volta é a casa do papel — nunca um segundo beco sem saída', () => {
    expect(homePathFor(platform)).toBe('/clientes');
    expect(homePathFor(admin)).toBe('/clientes');
    expect(homePathFor(manager)).toBe('/clientes');
    expect(homePathFor(clientManager)).toBe(`/clientes/${TENANT_A}`);
    expect(homePathFor(null)).toBe('/clientes');
  });

  it('rótulo do papel é PT-BR, nunca o enum cru', () => {
    expect(roleLabel(platform)).toBe('Administrador da plataforma');
    expect(roleLabel(clientManager)).toBe('Gerente do cliente');
    expect(roleLabel(clientOperator)).toBe('Operador do cliente');
    expect(roleLabel(admin)).toBe('Administrador');
    expect(roleLabel(manager)).toBe('Gerente');
  });
});

describe('a plataforma bem formada — espelho de `is_platform` (backend)', () => {
  it('linha `platform` com organização ou tenant é corrompida: não ganha alcance', () => {
    // O CHECK do banco recusa estas linhas; o front as trata como o backend —
    // negado por padrão, nunca "quase plataforma".
    const comOrg = { ...platform, organization_id: ORG_HOLOGRAM };
    const comTenant = { ...platform, client_id: TENANT_A };
    expect(isPlatformScoped(comOrg)).toBe(false);
    expect(isPlatformScoped(comTenant)).toBe(false);
    expect(isStaff(comOrg)).toBe(false);
    expect(canAccessClient(comOrg, TENANT_B)).toBe(false);
    expect(canSeeSystemArea(comTenant)).toBe(false);
  });
});

describe('organizationLabel — em que chapéu a pessoa está (86e36ecwa)', () => {
  it('a plataforma não tem organização: o rótulo é o escopo', () => {
    expect(organizationLabel(platform)).toBe('Plataforma');
  });

  it('staff e usuário de cliente mostram a organização da própria linha', () => {
    expect(organizationLabel(admin)).toBe('Hologram');
    expect(organizationLabel(clientManager)).toBe('Hologram');
    expect(organizationLabel({ ...admin, organization_name: 'Prospecta' })).toBe('Prospecta');
  });

  it('sem nome na linha, omite em vez de inventar', () => {
    // Estado inválido (o CHECK do banco não deixa acontecer fora da
    // plataforma): o header some com o rótulo, nunca mostra "undefined".
    expect(organizationLabel({ ...admin, organization_name: null })).toBeNull();
    expect(organizationLabel(null)).toBeNull();
  });

  it('organização é dimensão separada do papel: mesmo papel, chapéus diferentes', () => {
    const adminProspecta = {
      ...admin,
      organization_id: ORG_PROSPECTA,
      organization_name: 'Prospecta',
    };
    expect(roleLabel(adminProspecta)).toBe(roleLabel(admin));
    expect(organizationLabel(adminProspecta)).not.toBe(organizationLabel(admin));
  });
});

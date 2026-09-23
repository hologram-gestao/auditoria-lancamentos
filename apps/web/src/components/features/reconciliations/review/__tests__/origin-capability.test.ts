/**
 * `originCanWrite` — espelho declarado do predicado do servidor (S9 / R3 · R6).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 *
 * O predicado do backend é UM
 * (`modules/client_connections/capability.py::connection_supports`): **ativa E
 * o tipo declara a capacidade**. Este arquivo trava as duas metades — porque
 * esquecer qualquer uma produz o mesmo defeito, com sinais opostos: oferecer o
 * que dá 409, ou esconder o que funcionaria.
 */
import { describe, expect, it } from 'vitest';

import { originCanWrite } from '@/components/features/reconciliations/review/omie-posting-eligibility';
import type { ClientConnection } from '@/lib/contracts';

function connection(over: Partial<ClientConnection> = {}): ClientConnection {
  return {
    id: 'conn-1',
    provider_type: 'omie',
    label: 'Omie',
    status: 'ativa',
    last_checked_at: null,
    accounts_synced_at: null,
    capabilities: ['verificar_credencial', 'listar_contas', 'listar_lancamentos', 'escrever'],
    ...over,
  };
}

describe('originCanWrite', () => {
  it('conexão ativa que declara `escrever` → pode', () => {
    expect(originCanWrite([connection()])).toBe(true);
  });

  it('sem nenhuma conexão → não pode', () => {
    expect(originCanWrite([])).toBe(false);
  });

  it('`erro` e `inativa` NÃO são capazes, mesmo declarando `escrever`', () => {
    expect(originCanWrite([connection({ status: 'erro' })])).toBe(false);
    expect(originCanWrite([connection({ status: 'inativa' })])).toBe(false);
  });

  it('ativa sem a capacidade `escrever` → não pode (o caso do provedor de planilha)', () => {
    expect(
      originCanWrite([
        connection({
          provider_type: 'planilha',
          capabilities: ['verificar_credencial', 'listar_lancamentos'],
        }),
      ]),
    ).toBe(false);
  });

  it('basta UMA conexão capaz entre várias', () => {
    expect(
      originCanWrite([
        connection({ id: 'a', status: 'erro' }),
        connection({ id: 'b', capabilities: ['listar_contas'] }),
        connection({ id: 'c' }),
      ]),
    ).toBe(true);
  });
});

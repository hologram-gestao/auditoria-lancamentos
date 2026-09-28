/**
 * `lib/origin-capabilities.ts` — a decisão "a origem sabe fazer isto?" num
 * lugar só (Sprint 14 — FRONT 14.5).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 *
 * O que trava: o espelho de `connection_supports`/`select_capable_connection`
 * do backend (ativa E declara), a taxonomia em três com a ORDEM do
 * diagnóstico, o conservadorismo quando o detalhe não trouxe a lista, e a
 * regra "origem por arquivo" (a conexão capaz de listar lançamentos é `arquivo`).
 */
import { describe, expect, it } from 'vitest';

import type { ClientConnection } from '@/lib/contracts';
import {
  connectionSupports,
  fileConnectionOf,
  hasFileConnection,
  originCodeFor,
  originHasCapability,
  originIsFileBased,
  selectCapableConnection,
} from '@/lib/origin-capabilities';

function omie(over: Partial<ClientConnection> = {}): ClientConnection {
  return {
    id: 'omie-1',
    provider_type: 'omie',
    label: 'Omie',
    status: 'ativa',
    last_checked_at: null,
    accounts_synced_at: null,
    capabilities: [
      'verificar_credencial',
      'listar_contas',
      'listar_lancamentos',
      'escrever',
      'listar_titulos_em_aberto',
    ],
    ...over,
  };
}

function arquivo(over: Partial<ClientConnection> = {}): ClientConnection {
  return {
    id: 'arq-1',
    provider_type: 'arquivo',
    label: 'Arquivo',
    status: 'ativa',
    last_checked_at: null,
    accounts_synced_at: null,
    // O adaptador de arquivo declara SÓ listar lançamentos (BACK 14.1).
    capabilities: ['listar_lancamentos'],
    ...over,
  };
}

describe('connectionSupports / selectCapableConnection — espelho do servidor', () => {
  it('capaz = ativa E o tipo declara', () => {
    expect(connectionSupports(omie(), 'listar_contas')).toBe(true);
    expect(connectionSupports(omie({ status: 'erro' }), 'listar_contas')).toBe(false);
    expect(connectionSupports(omie({ status: 'inativa' }), 'listar_contas')).toBe(false);
    expect(connectionSupports(arquivo(), 'listar_contas')).toBe(false);
    expect(connectionSupports(arquivo(), 'listar_lancamentos')).toBe(true);
  });

  it('escolhe a PRIMEIRA ativa que declara, na ordem da lista', () => {
    const first = arquivo({ id: 'first' });
    const second = omie({ id: 'second' });
    expect(selectCapableConnection([first, second], 'listar_lancamentos')?.id).toBe('first');
    expect(selectCapableConnection([first, second], 'listar_contas')?.id).toBe('second');
    expect(selectCapableConnection([arquivo()], 'listar_contas')).toBeNull();
    expect(originHasCapability([arquivo(), omie({ status: 'erro' })], 'escrever')).toBe(false);
  });
});

describe('origem por arquivo', () => {
  it('fileConnectionOf/hasFileConnection acham a conexão `arquivo` em qualquer estado', () => {
    expect(fileConnectionOf([omie()])).toBeNull();
    expect(fileConnectionOf([omie(), arquivo({ status: 'erro' })])?.id).toBe('arq-1');
    expect(hasFileConnection([arquivo({ status: 'inativa' })])).toBe(true);
    expect(hasFileConnection([])).toBe(false);
  });

  it('originIsFileBased: a conexão capaz de listar lançamentos é `arquivo`', () => {
    expect(originIsFileBased([arquivo()])).toBe(true);
    expect(originIsFileBased([omie()])).toBe(false);
    // Com as duas ativas, a primeira da lista decide — como no servidor.
    expect(originIsFileBased([arquivo(), omie()])).toBe(true);
    expect(originIsFileBased([omie(), arquivo()])).toBe(false);
    // Arquivo fora do ar não alimenta nada: não é "por arquivo".
    expect(originIsFileBased([arquivo({ status: 'erro' })])).toBe(false);
  });
});

describe('originCodeFor — a taxonomia em três, na ordem do diagnóstico', () => {
  it('sem_origem → SEM_CONEXAO e erro → ORIGEM_COM_ERRO, seja qual for a lista', () => {
    expect(originCodeFor('sem_origem', [], 'listar_contas')).toBe('SEM_CONEXAO');
    expect(originCodeFor('sem_origem', undefined, 'listar_contas')).toBe('SEM_CONEXAO');
    expect(originCodeFor('erro', [omie({ status: 'erro' })], 'listar_contas')).toBe(
      'ORIGEM_COM_ERRO',
    );
  });

  it('ativa com alguma ativa que declara → liberado; nenhuma declara → CAPACIDADE_AUSENTE', () => {
    expect(originCodeFor('ativa', [omie()], 'listar_contas')).toBeNull();
    expect(originCodeFor('ativa', [arquivo()], 'listar_contas')).toBe('CAPACIDADE_AUSENTE');
    expect(originCodeFor('ativa', [arquivo()], 'listar_titulos_em_aberto')).toBe(
      'CAPACIDADE_AUSENTE',
    );
    expect(originCodeFor('ativa', [arquivo()], 'listar_lancamentos')).toBeNull();
    expect(originCodeFor('ativa', [arquivo(), omie()], 'listar_contas')).toBeNull();
  });

  it('não contradiz o servidor: sem a lista, ou lista sem ativa apesar de `ativa`, libera', () => {
    // Detalhe antigo em cache / fixture sem `connections`.
    expect(originCodeFor('ativa', undefined, 'listar_contas')).toBeNull();
    // Conexão legada sintetizada (S9): `origin_status` diz ativa e a lista vem vazia.
    expect(originCodeFor('ativa', [], 'listar_contas')).toBeNull();
    // Ativa no status, mas só inativas na lista — o status do servidor manda.
    expect(originCodeFor('ativa', [omie({ status: 'inativa' })], 'listar_contas')).toBeNull();
  });
});

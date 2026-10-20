/**
 * Quem pode ser lançada, em que forma e se a forma pode mudar (86e3n70p0 e
 * 86e3n70q0, bloco C).
 *
 * Espelhos declarados do servidor (`omie_posting/service.py`):
 *   - `processAllowsDirectPosting` ↔ `direto_indisponivel_no_modo_vencimento`;
 *   - `kindSwitchAllowed` ↔ `_kind_switch_allowed` (troca só sem dinheiro escrito);
 *   - `getPostingBlock` ↔ `_eligibility_block` + a intenção confirmada.
 */
import { describe, expect, it } from 'vitest';

import type { OmiePostingState } from '@/lib/contracts';

import {
  DIRECT_IN_DUE_DATE_PROCESS_MESSAGE,
  getPostingBlock,
  hasPendingInstallments,
  isProvisionStalled,
  kindSwitchAllowed,
  lockedPostingMode,
  postingBlockMessage,
  processAllowsDirectPosting,
} from '../omie-posting-eligibility';

function state(over: Partial<OmiePostingState> = {}): OmiePostingState {
  return {
    kind: 'provisao',
    status: 'pending',
    step: 'fornecedor',
    attempts: 0,
    supplier_code: null,
    payable_id: null,
    payment_id: null,
    omie_lancamento_id: null,
    error_code: null,
    error_message: null,
    ...over,
  };
}

const SEM_OMIE = { situation: 'sem_omie', omie_lancamento_id: null, amount: '-10.00' };
const DIRETO_RECUSADO = {
  kind: 'direto',
  step: null,
  status: 'failed',
  attempts: 1,
  error_code: 'OMIE_FAULT',
};

describe('processAllowsDirectPosting', () => {
  it('o processo da compra e a sessão antiga (sem processo gravado) aceitam o direto', () => {
    expect(processAllowsDirectPosting({ card_posting_date_mode: 'purchase_date' })).toBe(true);
    expect(processAllowsDirectPosting({ card_posting_date_mode: null })).toBe(true);
    expect(processAllowsDirectPosting({})).toBe(true);
  });

  it('o processo do vencimento não aceita, e a frase é a do servidor', () => {
    expect(processAllowsDirectPosting({ card_posting_date_mode: 'invoice_due_date' })).toBe(false);
    expect(DIRECT_IN_DUE_DATE_PROCESS_MESSAGE).toBe(
      'Esta conciliação cruza as compras pelo vencimento da fatura: o lançamento direto gravaria a data da compra. Provisione a compra no Contas a Pagar.',
    );
  });
});

describe('kindSwitchAllowed (espelho de _kind_switch_allowed)', () => {
  it('provisão ainda no passo do fornecedor pode trocar', () => {
    expect(kindSwitchAllowed(state())).toBe(true);
    expect(kindSwitchAllowed(state({ status: 'failed' }))).toBe(true);
  });

  it.each(['titulo', 'baixa'])('provisão no passo %s não troca (pode haver título)', (step) => {
    expect(kindSwitchAllowed(state({ step, supplier_code: 1 }))).toBe(false);
  });

  it('o que está confirmado nunca troca', () => {
    expect(kindSwitchAllowed(state({ status: 'confirmed', step: 'concluido' }))).toBe(false);
    expect(
      kindSwitchAllowed(
        state({ kind: 'direto', status: 'confirmed', step: null, omie_lancamento_id: 9 }),
      ),
    ).toBe(false);
  });

  it('direto recusado por faultstring na ÚNICA tentativa troca', () => {
    expect(kindSwitchAllowed(state(DIRETO_RECUSADO))).toBe(true);
  });

  it.each([
    ['duas tentativas', { attempts: 2 }],
    ['timeout, não fault', { error_code: 'OMIE_TIMEOUT' }],
    ['lançamento vinculado', { omie_lancamento_id: 7 }],
    ['ainda pendente', { status: 'pending' }],
  ])('direto não troca: %s', (_nome, over) => {
    expect(kindSwitchAllowed(state({ ...DIRETO_RECUSADO, ...over }))).toBe(false);
  });
});

describe('lockedPostingMode e isProvisionStalled', () => {
  it('sem intenção, a pessoa escolhe', () => {
    expect(lockedPostingMode(null)).toBeNull();
    expect(lockedPostingMode(undefined)).toBeNull();
  });

  it('provisão no título trava em provisionar; direto pendente trava em direto', () => {
    expect(lockedPostingMode(state({ step: 'titulo', supplier_code: 1 }))).toBe('provisionar');
    expect(
      lockedPostingMode(state({ kind: 'direto', step: null, status: 'pending', attempts: 1 })),
    ).toBe('direto');
  });

  it('provisão começada e não terminada está parada; a concluída não', () => {
    expect(
      isProvisionStalled(
        state({ status: 'failed', step: 'baixa', supplier_code: 1, payable_id: 2 }),
      ),
    ).toBe(true);
    expect(isProvisionStalled(state({ status: 'confirmed', step: 'concluido' }))).toBe(false);
    expect(isProvisionStalled(state({ kind: 'direto', step: null }))).toBe(false);
  });
});

describe('getPostingBlock com a intenção do servidor', () => {
  const PROVISIONADA = state({
    status: 'confirmed',
    step: 'concluido',
    supplier_code: 1,
    payable_id: 88,
  });

  it('provisão confirmada (linha ainda sem_omie) é "já lançada", com o número do título', () => {
    expect(getPostingBlock({ ...SEM_OMIE, omie_posting: PROVISIONADA }, { isCard: true })).toBe(
      'ja_lancada',
    );
    expect(postingBlockMessage('ja_lancada', PROVISIONADA)).toBe(
      'Esta compra já foi provisionada no Omie (título nº 88).',
    );
  });

  it('provisão parada continua elegível (é retomada)', () => {
    const parada = state({ status: 'failed', step: 'baixa', supplier_code: 1, payable_id: 88 });
    expect(getPostingBlock({ ...SEM_OMIE, omie_posting: parada }, { isCard: true })).toBeNull();
  });

  it('a ordem do servidor: o estorno vence a intenção confirmada', () => {
    expect(
      getPostingBlock(
        { ...SEM_OMIE, amount: '5.00', omie_posting: PROVISIONADA },
        { isCard: true },
      ),
    ).toBe('estorno_nao_verificado');
  });

  it('86e3qhhtb: provisão concluída com parcela futura sem título continua elegível', () => {
    const parcela = {
      numero: 9,
      total: 10,
      vencimento: '2026-11-10',
      valor: '100.00',
      payable_id: null,
      error_message: 'recusa',
    };
    const incompleta = { ...PROVISIONADA, installments: [{ ...parcela, step: 'titulo' }] };
    expect(hasPendingInstallments(incompleta)).toBe(true);
    expect(isProvisionStalled(incompleta)).toBe(true);
    expect(getPostingBlock({ ...SEM_OMIE, omie_posting: incompleta }, { isCard: true })).toBeNull();

    const completa = {
      ...PROVISIONADA,
      installments: [{ ...parcela, step: 'concluido', payable_id: 77, error_message: null }],
    };
    expect(hasPendingInstallments(completa)).toBe(false);
    expect(isProvisionStalled(completa)).toBe(false);
    expect(getPostingBlock({ ...SEM_OMIE, omie_posting: completa }, { isCard: true })).toBe(
      'ja_lancada',
    );
  });

  it('sem intenção, nada muda em relação à Sprint 7', () => {
    expect(getPostingBlock(SEM_OMIE, { isCard: true })).toBeNull();
    expect(postingBlockMessage('ja_lancada')).toBe(
      'Esta linha já está vinculada a um lançamento do Omie.',
    );
  });
});

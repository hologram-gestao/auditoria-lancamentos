/**
 * A regra da linha da gaveta "Lançar no Omie" e o pedido que sai dela
 * (86e3n70q0, bloco C; a divisão em categorias fecha a 86e3nb818).
 *
 * Espelho de `omie_posting/schemas.py` e de `_category_split`/`_department_split`:
 * tolerância de 1 centavo e de 0,01 ponto percentual, de 2 a 10 categorias, até 30
 * departamentos, código repetido recusado.
 */
import { describe, expect, it } from 'vitest';

import {
  amountToCents,
  buildPostingLine,
  categoryRemainderCents,
  departmentPartCents,
  emptyDraft,
  lineIssue,
  type LineContext,
  type LineDraft,
} from '../omie-posting';

const CTX: LineContext = {
  amount: '-103.80',
  departmentRequired: false,
  hasDefaultSupplier: false,
  supplierSettled: false,
};
const FOUND = { codigo: 7, razao_social: 'X', nome_fantasia: null };

function draft(over: Partial<LineDraft> = {}): LineDraft {
  return {
    ...emptyDraft('provisionar'),
    categoria: '2.02.02',
    supplier: { kind: 'found', cnpj: '12345678000195', supplier: FOUND },
    ...over,
  };
}

function parts(...values: [string | null, string][]) {
  return values.map(([codigo, cents], i) => ({ key: `k${i}`, codigo, cents }));
}

describe('dinheiro em centavos', () => {
  it('lê o valor do servidor sem float', () => {
    expect(amountToCents('-103.80')).toBe(10380);
    expect(amountToCents('0.1')).toBe(10);
    expect(amountToCents('12')).toBe(1200);
  });

  it('o que falta para fechar e o valor de exibição do rateio', () => {
    expect(categoryRemainderCents(parts(['a', '10000'], ['b', '300']), '-103.80')).toBe(80);
    expect(departmentPartCents('3333', '-100.00')).toBe(3333);
  });
});

describe('lineIssue', () => {
  it('linha completa não tem problema', () => {
    expect(lineIssue(draft(), CTX)).toBeNull();
  });

  it('sem categoria', () => {
    expect(lineIssue(draft({ categoria: null }), CTX)).toBe('sem_categoria');
  });

  it('divisão: fecha com 1 centavo de folga, não com 2', () => {
    const ok = draft({ split: true, parts: parts(['2.02.02', '10000'], ['2.09.09', '379']) });
    expect(lineIssue(ok, CTX)).toBeNull();
    const nao = draft({ split: true, parts: parts(['2.02.02', '10000'], ['2.09.09', '378']) });
    expect(lineIssue(nao, CTX)).toBe('categorias_nao_fecham');
  });

  it('divisão: menos de 2 partes, parte vazia ou categoria repetida', () => {
    expect(lineIssue(draft({ split: true, parts: parts(['a', '10380']) }), CTX)).toBe(
      'categorias_incompletas',
    );
    expect(lineIssue(draft({ split: true, parts: parts(['a', '10380'], [null, '']) }), CTX)).toBe(
      'categorias_incompletas',
    );
    expect(lineIssue(draft({ split: true, parts: parts(['a', '10000'], ['a', '380']) }), CTX)).toBe(
      'categoria_repetida',
    );
  });

  it('rateio: soma 100 com 0,01 de folga', () => {
    const deps = (a: string, b: string) => [
      { key: '1', codigo: 'd1', hundredths: a },
      { key: '2', codigo: 'd2', hundredths: b },
    ];
    expect(lineIssue(draft({ departments: deps('6000', '4000') }), CTX)).toBeNull();
    expect(lineIssue(draft({ departments: deps('6000', '3999') }), CTX)).toBeNull();
    expect(lineIssue(draft({ departments: deps('6000', '3998') }), CTX)).toBe(
      'departamentos_nao_fecham',
    );
  });

  it('rateio obrigatório sem rateio: departamento pendente', () => {
    expect(lineIssue(draft(), { ...CTX, departmentRequired: true })).toBe('departamento_pendente');
  });

  it('fornecedor: nenhum, padrão sem padrão no cliente, cadastro novo incompleto', () => {
    expect(lineIssue(draft({ supplier: { kind: 'none' } }), CTX)).toBe('fornecedor_pendente');
    expect(lineIssue(draft({ supplier: { kind: 'default' } }), CTX)).toBe('fornecedor_pendente');
    expect(
      lineIssue(draft({ supplier: { kind: 'default' } }), { ...CTX, hasDefaultSupplier: true }),
    ).toBeNull();
    expect(
      lineIssue(
        draft({ supplier: { kind: 'new', cnpj: '11222333000181', razaoSocial: 'X', email: 'x' } }),
        CTX,
      ),
    ).toBe('fornecedor_incompleto');
  });

  it('retomada depois do passo do fornecedor não pede fornecedor', () => {
    expect(
      lineIssue(draft({ supplier: { kind: 'none' } }), { ...CTX, supplierSettled: true }),
    ).toBeNull();
  });

  it('direto só precisa da categoria', () => {
    expect(lineIssue({ ...emptyDraft('direto'), categoria: '1.01.01' }, CTX)).toBeNull();
    expect(
      lineIssue(
        { ...emptyDraft('direto'), categoria: '1.01.01' },
        { ...CTX, departmentRequired: true },
      ),
    ).toBeNull();
  });
});

describe('buildPostingLine', () => {
  it('direto sai SÓ com file_entry_id, cod_categoria e modo, mesmo com resto preenchido', () => {
    const line = buildPostingLine(
      'e1',
      draft({
        modo: 'direto',
        categoria: '1.01.01',
        split: true,
        parts: parts(['a', '1'], ['b', '2']),
        departments: [{ key: '1', codigo: 'd1', hundredths: '10000' }],
      }),
      { supplierSettled: false },
    );
    expect(line).toEqual({ file_entry_id: 'e1', cod_categoria: '1.01.01', modo: 'direto' });
  });

  it('provisão dividida, com cadastro novo e rateio', () => {
    const line = buildPostingLine(
      'e1',
      draft({
        split: true,
        parts: parts(['2.02.02', '10000'], ['2.09.09', '380']),
        supplier: {
          kind: 'new',
          cnpj: '11.222.333/0001-81',
          razaoSocial: ' Loja Nova ',
          email: 'a@b.com ',
        },
        departments: [
          { key: '1', codigo: 'd1', hundredths: '3333' },
          { key: '2', codigo: 'd2', hundredths: '6667' },
        ],
      }),
      { supplierSettled: false },
    );
    expect(line).toEqual({
      file_entry_id: 'e1',
      modo: 'provisionar',
      categorias: [
        { codigo: '2.02.02', valor: '100.00' },
        { codigo: '2.09.09', valor: '3.80' },
      ],
      cnpj_fornecedor: '11222333000181',
      razao_social: 'Loja Nova',
      email: 'a@b.com',
      departamentos: [
        { codigo: 'd1', percentual: '33.33' },
        { codigo: 'd2', percentual: '66.67' },
      ],
    });
  });

  it('fornecedor padrão e retomada não mandam CNPJ', () => {
    expect(
      buildPostingLine('e1', draft({ supplier: { kind: 'default' } }), { supplierSettled: false }),
    ).toEqual({ file_entry_id: 'e1', modo: 'provisionar', cod_categoria: '2.02.02' });
    expect(buildPostingLine('e1', draft(), { supplierSettled: true })).toEqual({
      file_entry_id: 'e1',
      modo: 'provisionar',
      cod_categoria: '2.02.02',
    });
  });
});

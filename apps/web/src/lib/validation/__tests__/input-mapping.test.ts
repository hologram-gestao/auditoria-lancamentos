/**
 * Schema do mapeamento de entrada e as conversões formulário ↔ contrato
 * (Sprint 14 — FRONT 14.5 / R1).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 *
 * Espelha `InputMappingFields._coherent` do backend, regra a regra: convenção
 * de sinal OBRIGATÓRIA; `valor_com_sinal` e `coluna_natureza` exigem a coluna
 * de valor; `coluna_natureza` exige natureza + literais diferentes;
 * `colunas_separadas` exige as duas colunas diferentes e dispensa o valor; CSV
 * exige delimitador e codificação; classificação livre exige a coluna. E a
 * conversão para o `PUT` só manda os campos da convenção escolhida — o
 * servidor recusa `natureColumn` em `valor_com_sinal`.
 */
import { describe, expect, it } from 'vitest';

import type { InputMapping } from '@/lib/contracts';
import {
  defaultInputMappingFormValues,
  fromInputMapping,
  mappedColumns,
  toInputMappingRequest,
  type InputMappingFormValues,
} from '@/lib/input-mapping';
import { inputMappingFormSchema } from '@/lib/validation/input-mapping';

function values(over: Partial<InputMappingFormValues> = {}): InputMappingFormValues {
  return {
    ...defaultInputMappingFormValues('xlsx'),
    dateColumn: 'Data',
    descriptionColumn: 'Histórico',
    amountColumn: 'Valor',
    signConvention: 'valor_com_sinal',
    ...over,
  };
}

function issuesOf(input: InputMappingFormValues): Record<string, string> {
  const result = inputMappingFormSchema.safeParse(input);
  if (result.success) return {};
  const out: Record<string, string> = {};
  for (const issue of result.error.issues) out[String(issue.path[0])] = issue.message;
  return out;
}

describe('inputMappingFormSchema — as regras do backend, uma a uma', () => {
  it('aceita o mínimo válido: data, descrição, valor e valor_com_sinal', () => {
    expect(issuesOf(values())).toEqual({});
  });

  it('a convenção de sinal é obrigatória — nunca inferida', () => {
    expect(issuesOf(values({ signConvention: '' })).signConvention).toBe(
      'Declare como o arquivo indica débito e crédito.',
    );
  });

  it('data e descrição são obrigatórias', () => {
    const issues = issuesOf(values({ dateColumn: '', descriptionColumn: '   ' }));
    expect(issues.dateColumn).toBe('Escolha a coluna da data.');
    expect(issues.descriptionColumn).toBe('Escolha a coluna da descrição.');
  });

  it('valor_com_sinal e coluna_natureza exigem a coluna de valor', () => {
    expect(issuesOf(values({ amountColumn: '' })).amountColumn).toBe('Escolha a coluna do valor.');
    expect(
      issuesOf(
        values({
          signConvention: 'coluna_natureza',
          amountColumn: '',
          natureColumn: 'D/C',
          debitValue: 'D',
          creditValue: 'C',
        }),
      ).amountColumn,
    ).toBe('Escolha a coluna do valor.');
  });

  it('coluna_natureza exige natureza e os dois literais, diferentes entre si', () => {
    const missing = issuesOf(values({ signConvention: 'coluna_natureza' }));
    expect(missing.natureColumn).toBeDefined();
    expect(missing.debitValue).toBeDefined();
    expect(missing.creditValue).toBeDefined();

    const same = issuesOf(
      values({
        signConvention: 'coluna_natureza',
        natureColumn: 'Tipo',
        debitValue: 'D',
        creditValue: 'D',
      }),
    );
    expect(same.creditValue).toBe('Os textos de débito e de crédito precisam ser diferentes.');

    expect(
      issuesOf(
        values({
          signConvention: 'coluna_natureza',
          natureColumn: 'Tipo',
          debitValue: 'D',
          creditValue: 'C',
        }),
      ),
    ).toEqual({});
  });

  it('colunas_separadas exige débito e crédito diferentes e dispensa a coluna de valor', () => {
    const missing = issuesOf(values({ signConvention: 'colunas_separadas', amountColumn: '' }));
    expect(missing.debitColumn).toBe('Escolha a coluna de débito.');
    expect(missing.creditColumn).toBe('Escolha a coluna de crédito.');
    expect(missing.amountColumn).toBeUndefined();

    const same = issuesOf(
      values({
        signConvention: 'colunas_separadas',
        amountColumn: '',
        debitColumn: 'Saída',
        creditColumn: 'Saída',
      }),
    );
    expect(same.creditColumn).toBe('As colunas de débito e de crédito precisam ser diferentes.');
  });

  it('CSV exige delimitador e codificação; XLSX não', () => {
    const csv = issuesOf(values({ fileFormat: 'csv', csvDelimiter: '', encoding: '' }));
    expect(csv.csvDelimiter).toBe('Declare o delimitador do CSV.');
    expect(csv.encoding).toBe('Declare a codificação do CSV.');
    expect(issuesOf(values({ fileFormat: 'csv', csvDelimiter: ';', encoding: 'utf-8' }))).toEqual(
      {},
    );
    expect(issuesOf(values({ fileFormat: 'xlsx', csvDelimiter: '', encoding: '' }))).toEqual({});
  });

  it('classificação livre exige a coluna de categoria', () => {
    expect(
      issuesOf(values({ categoryMode: 'classificacao_livre', categoryColumn: '' })).categoryColumn,
    ).toBeDefined();
    expect(
      issuesOf(values({ categoryMode: 'classificacao_livre', categoryColumn: 'Obs' })),
    ).toEqual({});
  });
});

describe('toInputMappingRequest — só os campos da convenção vão ao servidor', () => {
  it('sem convenção de sinal LANÇA em vez de presumir uma (sinal não se infere)', () => {
    expect(() => toInputMappingRequest(values({ signConvention: '' }))).toThrow(
      /Convenção de sinal não declarada/,
    );
  });

  it('valor_com_sinal zera natureza e colunas separadas, mesmo que o formulário as tenha', () => {
    const body = toInputMappingRequest(
      values({ natureColumn: 'Tipo', debitValue: 'D', debitColumn: 'Saída' }),
    );
    expect(body).toMatchObject({
      fileFormat: 'xlsx',
      csvDelimiter: null,
      encoding: null,
      dateColumn: 'Data',
      descriptionColumn: 'Histórico',
      amountColumn: 'Valor',
      signConvention: 'valor_com_sinal',
      natureColumn: null,
      debitValue: null,
      creditValue: null,
      debitColumn: null,
      creditColumn: null,
      categoryColumn: null,
      accountColumn: null,
      documentColumn: null,
      categoryMode: 'coluna_categoria',
    });
  });

  it('colunas_separadas zera a coluna de valor; coluna_natureza leva os literais', () => {
    expect(
      toInputMappingRequest(
        values({
          signConvention: 'colunas_separadas',
          amountColumn: 'Valor',
          debitColumn: 'Saída',
          creditColumn: 'Entrada',
        }),
      ),
    ).toMatchObject({ amountColumn: null, debitColumn: 'Saída', creditColumn: 'Entrada' });
    expect(
      toInputMappingRequest(
        values({
          signConvention: 'coluna_natureza',
          natureColumn: ' Tipo ',
          debitValue: 'D',
          creditValue: 'C',
        }),
      ),
    ).toMatchObject({
      amountColumn: 'Valor',
      natureColumn: 'Tipo',
      debitValue: 'D',
      creditValue: 'C',
      debitColumn: null,
    });
  });

  it('CSV leva delimitador e codificação; XLSX manda os dois null', () => {
    expect(
      toInputMappingRequest(values({ fileFormat: 'csv', csvDelimiter: '|', encoding: 'latin-1' })),
    ).toMatchObject({ csvDelimiter: '|', encoding: 'latin-1' });
    expect(
      toInputMappingRequest(values({ fileFormat: 'xlsx', csvDelimiter: ';', encoding: 'utf-8' })),
    ).toMatchObject({ csvDelimiter: null, encoding: null });
  });
});

const SAVED: InputMapping = {
  id: 'map-1',
  fileFormat: 'csv',
  csvDelimiter: ';',
  encoding: 'utf-8-sig',
  dateColumn: 'Data',
  descriptionColumn: 'Histórico',
  amountColumn: 'Valor',
  categoryColumn: 'Categoria',
  categoryMode: 'coluna_categoria',
  accountColumn: null,
  documentColumn: 'Doc',
  dateFormat: 'dd/mm/yyyy',
  decimalSeparator: ',',
  signConvention: 'coluna_natureza',
  natureColumn: 'D/C',
  debitValue: 'D',
  creditValue: 'C',
  debitColumn: null,
  creditColumn: null,
  createdAt: '2026-09-01T12:00:00Z',
  updatedAt: '2026-09-20T12:00:00Z',
};

describe('fromInputMapping / mappedColumns — o salvo volta para o formulário e o resumo', () => {
  it('ida e volta preserva o mapeamento', () => {
    const form = fromInputMapping(SAVED);
    expect(form.accountColumn).toBe('');
    expect(form.signConvention).toBe('coluna_natureza');
    expect(inputMappingFormSchema.safeParse(form).success).toBe(true);
    expect(toInputMappingRequest(form)).toMatchObject({
      fileFormat: 'csv',
      csvDelimiter: ';',
      encoding: 'utf-8-sig',
      natureColumn: 'D/C',
      debitValue: 'D',
      creditValue: 'C',
      accountColumn: null,
      documentColumn: 'Doc',
    });
  });

  it('o resumo lista só as colunas em uso, na ordem de leitura', () => {
    expect(mappedColumns(SAVED).map((p) => `${p.field}=${p.column}`)).toEqual([
      'dateColumn=Data',
      'descriptionColumn=Histórico',
      'amountColumn=Valor',
      'natureColumn=D/C',
      'categoryColumn=Categoria',
      'documentColumn=Doc',
    ]);
    expect(
      mappedColumns({
        ...SAVED,
        signConvention: 'colunas_separadas',
        amountColumn: null,
        natureColumn: null,
        debitColumn: 'Saída',
        creditColumn: 'Entrada',
      }).map((p) => p.field),
    ).toEqual([
      'dateColumn',
      'descriptionColumn',
      'debitColumn',
      'creditColumn',
      'categoryColumn',
      'documentColumn',
    ]);
  });
});

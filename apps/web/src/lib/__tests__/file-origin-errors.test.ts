/**
 * `lib/file-origin-errors.ts` — o leitor tipado das recusas do arquivo
 * (Sprint 14 — FRONT 14.6 / R5).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 *
 * O que trava: cada código da BACK 14.3 sai ESTREITADO com o `details` que o
 * servidor manda (colunas, linhas, totais); lixo no `details` é descartado, não
 * exibido; código fora da lista devolve `null` (o caller cai no toast).
 */
import { describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/api/client';
import { isFileRefusal, lineReasonLabel, readFileRefusal } from '@/lib/file-origin-errors';

function apiError(status: number, code: string, details?: Record<string, unknown>): ApiError {
  return new ApiError(status, { code, message: 'x', userMessage: `msg ${code}`, details });
}

describe('readFileRefusal', () => {
  it('CABECALHO_DIVERGENTE traz as colunas ausentes e as encontradas', () => {
    const refusal = readFileRefusal(
      apiError(422, 'CABECALHO_DIVERGENTE', {
        missingColumns: ['Histórico', 'Valor'],
        foundColumns: ['Data', 'Descrição', 'Valor', 42],
      }),
    );
    expect(refusal).toEqual({
      code: 'CABECALHO_DIVERGENTE',
      userMessage: 'msg CABECALHO_DIVERGENTE',
      missingColumns: ['Histórico', 'Valor'],
      // O que não é string é descartado, nunca renderizado.
      foundColumns: ['Data', 'Descrição', 'Valor'],
    });
  });

  it('LINHAS_INVALIDAS traz linha × motivo e o total (o servidor recorta em K)', () => {
    const refusal = readFileRefusal(
      apiError(422, 'LINHAS_INVALIDAS', {
        lines: [
          { line: 7, reason: 'valor_nao_numerico' },
          { line: 12, reason: 'data_invalida' },
          { line: 'x', reason: 'data_invalida' },
          null,
        ],
        total: 5,
      }),
    );
    expect(refusal).toEqual({
      code: 'LINHAS_INVALIDAS',
      userMessage: 'msg LINHAS_INVALIDAS',
      lines: [
        { line: 7, reason: 'valor_nao_numerico' },
        { line: 12, reason: 'data_invalida' },
      ],
      total: 5,
    });
  });

  it('LINHAS_INVALIDAS sem `total` usa o tamanho da lista', () => {
    const refusal = readFileRefusal(
      apiError(422, 'LINHAS_INVALIDAS', {
        lines: [{ line: 3, reason: 'campo_obrigatorio_vazio' }],
      }),
    );
    expect(refusal?.code === 'LINHAS_INVALIDAS' && refusal.total).toBe(1);
  });

  it('TOTAL_DIVERGENTE traz os dois totais como TEXTO', () => {
    const refusal = readFileRefusal(
      apiError(422, 'TOTAL_DIVERGENTE', { declaredTotal: '1000.00', computedTotal: '-980.50' }),
    );
    expect(refusal).toEqual({
      code: 'TOTAL_DIVERGENTE',
      userMessage: 'msg TOTAL_DIVERGENTE',
      declaredTotal: '1000.00',
      computedTotal: '-980.50',
    });
  });

  it('SEM_MAPEAMENTO traz as colunas encontradas para conduzir a criação', () => {
    const refusal = readFileRefusal(
      apiError(409, 'SEM_MAPEAMENTO', { foundColumns: ['Data', 'Histórico'] }),
    );
    expect(refusal).toEqual({
      code: 'SEM_MAPEAMENTO',
      userMessage: 'msg SEM_MAPEAMENTO',
      foundColumns: ['Data', 'Histórico'],
    });
  });

  it.each([
    'SINAL_NAO_DECLARADO',
    'FORMATO_NAO_SUPORTADO',
    'ARQUIVO_INVALIDO',
    'ARQUIVO_JA_PROCESSADO',
  ] as const)('%s sai só com o userMessage tipado', (code) => {
    expect(readFileRefusal(apiError(422, code))).toEqual({ code, userMessage: `msg ${code}` });
  });

  it('código fora da lista (e erro que não é ApiError) devolve null', () => {
    expect(readFileRefusal(apiError(409, 'SEM_CONEXAO'))).toBeNull();
    expect(readFileRefusal(apiError(500, 'INTERNAL_ERROR'))).toBeNull();
    expect(readFileRefusal(new Error('rede'))).toBeNull();
    expect(readFileRefusal(null)).toBeNull();
    expect(isFileRefusal(apiError(422, 'ARQUIVO_INVALIDO'))).toBe(true);
    expect(isFileRefusal(apiError(422, 'QUALQUER'))).toBe(false);
  });
});

describe('lineReasonLabel — vocabulário fechado, com fallback cru', () => {
  it('rotula os seis motivos do leitor e devolve o cru para o desconhecido', () => {
    expect(lineReasonLabel('valor_nao_numerico')).toBe('Valor não numérico');
    expect(lineReasonLabel('data_invalida')).toBe('Data inválida');
    expect(lineReasonLabel('campo_obrigatorio_vazio')).toBe('Campo obrigatório vazio');
    expect(lineReasonLabel('natureza_desconhecida')).toBe('Natureza (débito/crédito) desconhecida');
    expect(lineReasonLabel('data_fora_da_competencia')).toBe('Data fora da competência informada');
    expect(lineReasonLabel('campo_longo_demais')).toBe('Campo longo demais');
    expect(lineReasonLabel('motivo_futuro')).toBe('motivo_futuro');
  });
});

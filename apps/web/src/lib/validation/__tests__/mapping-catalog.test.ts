/**
 * Leitura das linhas coladas no "Adicionar alvos" (86e3n70pn): o espelho do
 * `MappingTargetCreate` do servidor, para o erro apontar a LINHA certa.
 */
import { describe, expect, it } from 'vitest';

import {
  MAX_TARGET_CODE_CHARS,
  parseTargetLines,
  targetLinesFormSchema,
} from '@/lib/validation/mapping-catalog';

describe('parseTargetLines', () => {
  it('aceita ponto e vírgula e TAB, ignora linha em branco e colapsa espaços do nome', () => {
    expect(parseTargetLines('3.01; Receita   bruta\r\n\n  \n3.02\tDeduções; abatimentos')).toEqual({
      targets: [
        { code: '3.01', name: 'Receita bruta' },
        { code: '3.02', name: 'Deduções; abatimentos' },
      ],
      problems: [],
    });
  });

  it('nomeia a linha de cada problema', () => {
    const longCode = 'x'.repeat(MAX_TARGET_CODE_CHARS + 1);
    const { targets, problems } = parseTargetLines(
      [
        'sem separador',
        ';sem código',
        '3 01;espaço',
        `${longCode};longo`,
        '3.01;',
        '3.02;ok',
        '3.02;de novo',
      ].join('\n'),
    );
    expect(targets).toEqual([{ code: '3.02', name: 'ok' }]);
    expect(problems).toEqual([
      { line: 1, message: 'use "código;nome"' },
      { line: 2, message: 'falta o código' },
      { line: 3, message: 'o código não pode ter espaço' },
      { line: 4, message: `código com mais de ${MAX_TARGET_CODE_CHARS} caracteres` },
      { line: 5, message: 'falta o nome' },
      { line: 7, message: 'código 3.02 repetido (linha 6)' },
    ]);
  });
});

describe('targetLinesFormSchema', () => {
  it('texto vazio pede ao menos uma linha', () => {
    const result = targetLinesFormSchema.safeParse({ lines: '\n  \n' });
    expect(result.success).toBe(false);
    expect(result.error?.issues[0]?.message).toBe(
      'Cole ao menos uma linha no formato código;nome.',
    );
  });

  it('mais de 500 alvos é recusado como o lote do servidor', () => {
    const lines = Array.from({ length: 501 }, (_, i) => `c${i};Alvo ${i}`).join('\n');
    expect(targetLinesFormSchema.safeParse({ lines }).success).toBe(false);
  });
});

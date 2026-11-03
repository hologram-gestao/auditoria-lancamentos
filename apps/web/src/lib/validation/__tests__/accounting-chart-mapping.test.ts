import { describe, expect, it } from 'vitest';

import {
  accountingChartMappingSchema,
  suggestedRole,
  toMappingPayload,
  type AccountingChartMappingValues,
} from '../accounting-chart-mapping';

function messages(values: AccountingChartMappingValues): string[] {
  const result = accountingChartMappingSchema.safeParse(values);
  return result.success ? [] : result.error.issues.map((issue) => issue.message);
}

describe('mapeamento das colunas do plano (86e3ydjz4), a regra do servidor', () => {
  it('código, nome e tipo escolhidos: passa e vira o corpo do pedido sem as ignoradas', () => {
    const values: AccountingChartMappingValues = {
      roles: ['codigo', 'ignorar', 'tipo', 'nome'],
      tipoPelaHierarquia: false,
    };
    expect(messages(values)).toEqual([]);
    expect(toMappingPayload(values)).toEqual({
      colunas: { codigo: 0, tipo: 2, nome: 3 },
      tipoPelaHierarquia: false,
    });
  });

  it('código e nome são obrigatórios', () => {
    expect(messages({ roles: ['tipo', 'ignorar'], tipoPelaHierarquia: false })).toEqual([
      'Escolha a coluna de código.',
      'Escolha a coluna de nome.',
    ]);
  });

  it('um papel por coluna', () => {
    expect(
      messages({ roles: ['codigo', 'nome', 'nome', 'tipo'], tipoPelaHierarquia: false }),
    ).toContain('Só uma coluna pode ser nome.');
  });

  it('sem tipo, só com a hierarquia; a hierarquia exige classificação e nenhum tipo', () => {
    expect(messages({ roles: ['codigo', 'nome'], tipoPelaHierarquia: false })).toContain(
      'Escolha a coluna de tipo (S ou A), ou marque que o arquivo não tem coluna de tipo.',
    );
    expect(messages({ roles: ['codigo', 'nome'], tipoPelaHierarquia: true })).toHaveLength(1);
    expect(
      messages({ roles: ['codigo', 'classificacao', 'nome', 'tipo'], tipoPelaHierarquia: true }),
    ).toHaveLength(1);
    expect(
      messages({ roles: ['codigo', 'classificacao', 'nome'], tipoPelaHierarquia: true }),
    ).toEqual([]);
  });

  it('o papel sugerido só pré-marca papel de conta; o resto começa ignorado', () => {
    expect(suggestedRole('codigo')).toBe('codigo');
    expect(suggestedRole('documento')).toBe('ignorar');
    expect(suggestedRole(null)).toBe('ignorar');
  });
});

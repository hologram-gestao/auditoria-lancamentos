/**
 * O gráfico do fluxo previsto (86e3k1q54): cinco grupos de barras (vencidos fica
 * fora), eixo a partir do máximo da série, "sem títulos" na faixa vazia, cor por
 * token e a tabela `sr-only` com a série inteira.
 */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  axisLabel,
  FlowChart,
  gridValues,
  niceStep,
} from '@/components/features/clients/dashboard/flow-chart';
import type { TitlesFlowBucket } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

function bucket(
  code: TitlesFlowBucket['bucket'],
  receber: string,
  pagar: string,
  counts: [number, number] = [1, 1],
): TitlesFlowBucket {
  return {
    bucket: code,
    aReceber: { total: receber, count: counts[0] },
    aPagar: { total: pagar, count: counts[1] },
    net: String(Number(receber) - Number(pagar)),
  };
}

const BUCKETS: TitlesFlowBucket[] = [
  bucket('vencidos', '900000.00', '100.00'),
  bucket('ate_7', '3000.00', '1200.00'),
  bucket('8_30', '9000.00', '3800.00'),
  bucket('31_60', '12000.00', '0.00', [1, 0]),
  bucket('61_90', '0.00', '0.00', [0, 0]),
  bucket('90_mais', '500.00', '700.00'),
];

describe('escala do eixo', () => {
  it('passo redondo e três linhas a partir do máximo', () => {
    expect(niceStep(4000)).toBe(5000);
    expect(niceStep(1500)).toBe(2000);
    expect(niceStep(2400)).toBe(2500);
    expect(niceStep(0)).toBe(0);
    expect(gridValues(12000)).toEqual([5000, 10000, 15000]);
    expect(gridValues(0)).toEqual([]);
  });

  it('rótulos "R$ N mil" e abaixo de mil em reais', () => {
    expect(axisLabel(5000)).toBe('R$ 5 mil');
    expect(axisLabel(2500)).toBe('R$ 2,5 mil');
    expect(axisLabel(800)).toBe('R$ 800');
  });
});

describe('FlowChart', () => {
  it('cinco grupos (vencidos fica fora) com a receber em info e a pagar em warning', () => {
    render(<FlowChart buckets={BUCKETS} />);
    const svg = screen.getByTestId('flow-chart-svg');
    const groups = within(svg as unknown as HTMLElement).getAllByTestId('flow-chart-group');

    expect(groups.map((g) => g.getAttribute('data-bucket'))).toEqual([
      'ate_7',
      '8_30',
      '31_60',
      '61_90',
      '90_mais',
    ]);
    const [receber, pagar] = Array.from(groups[0]!.querySelectorAll('rect'));
    expect(receber!.getAttribute('style')).toContain('hsl(var(--info))');
    expect(pagar!.getAttribute('style')).toContain('hsl(var(--warning))');
    // O vencido (900 mil) não entrou na escala: o eixo vem do maior FUTURO (12 mil).
    expect(screen.getAllByTestId('flow-chart-axis-label').map((el) => el.textContent)).toEqual([
      'R$ 5 mil',
      'R$ 10 mil',
      'R$ 15 mil',
    ]);
    expect(screen.getAllByTestId('flow-chart-grid')).toHaveLength(3);
  });

  it('faixa sem título nenhum mostra "sem títulos"', () => {
    render(<FlowChart buckets={BUCKETS} />);
    expect(screen.getAllByText('sem títulos')).toHaveLength(1);
  });

  it('tabela sr-only com faixa, a receber, a pagar e líquido; svg com resumo', async () => {
    const { container } = render(<FlowChart buckets={BUCKETS} />);
    const table = screen.getByRole('table', { name: 'Fluxo previsto por faixa de vencimento' });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(6); // cabeçalho + cinco faixas
    expect(rows[1]).toHaveTextContent(
      /Até 7 dias.*R\$\s*3\.000,00.*R\$\s*1\.200,00.*\+R\$\s*1\.800,00/,
    );
    expect(screen.getByRole('img')).toHaveAccessibleName(/Até 7 dias: R\$\s*3\.000,00 a receber/);
    await assertNoA11yViolations(container);
  });
});

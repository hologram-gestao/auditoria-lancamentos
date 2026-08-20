/**
 * Aba Resumo mostra os números do BACKEND — e não soma nada (86e2u513f).
 *
 * A soma no navegador cobria as 50 primeiras linhas; o componente agora é
 * apresentação pura das somas da sessão inteira. O heurístico de encargos
 * (IOF/juros/multa) migrou junto com a conta — seus casos vivem em
 * `apps/api/tests/unit/test_charge_description.py`.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SummaryTab } from '@/components/features/reconciliations/review/summary-tab';

const BASE = {
  totalFileEntries: 120,
  counts: { conciliated: 97, semOmie: 20, omieSemArquivo: 3, anomaly: 4 },
  anomaliesBreakdown: { critical: 2, moderate: 1, info: 1, resolved: 2 },
  referenceMonthLabel: 'Julho de 2026',
  balances: {
    start: '1000.00',
    endFile: '1500.00',
    endOmie: '1500.00',
    difference: '0.00',
  },
};

describe('SummaryTab — números do backend, sessão inteira', () => {
  it('conta corrente: créditos e débitos vêm das props, sem aviso de truncamento', () => {
    render(
      <SummaryTab
        {...BASE}
        isCard={false}
        amounts={{ credits: '62.20', debits: '294.80', cardCharges: null }}
      />,
    );
    // formatBRL usa espaço não-quebrável entre R$ e o número.
    expect(screen.getByText('Total créditos')).toBeInTheDocument();
    expect(screen.getByText(/R\$\s*62,20/)).toBeInTheDocument();
    expect(screen.getByText(/R\$\s*294,80/)).toBeInTheDocument();
    // O aviso morreu junto com a causa — nenhuma menção a "primeiras".
    expect(screen.queryByText(/primeiras/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/considera\s+apenas/i)).not.toBeInTheDocument();
  });

  it('cartão: compras/estornos/encargos do backend, com o hint fixo do encargo', () => {
    render(
      <SummaryTab
        {...BASE}
        isCard={true}
        amounts={{ credits: '5.00', debits: '120.50', cardCharges: '20.50' }}
      />,
    );
    expect(screen.getByText('Total de compras')).toBeInTheDocument();
    expect(screen.getByText(/R\$\s*120,50/)).toBeInTheDocument();
    expect(screen.getByText(/R\$\s*20,50/)).toBeInTheDocument();
    expect(screen.getByText('IOF, juros e multa (por descrição)')).toBeInTheDocument();
    expect(screen.queryByText(/primeiras/i)).not.toBeInTheDocument();
  });

  it('breakdown de anomalias reflete a sessão inteira (props, não página)', () => {
    render(
      <SummaryTab
        {...BASE}
        isCard={false}
        amounts={{ credits: '0', debits: '0', cardCharges: null }}
      />,
    );
    expect(screen.getByText('Críticas').closest('div')).toHaveTextContent('2');
    expect(screen.getByText('Moderadas').closest('div')).toHaveTextContent('1');
    expect(screen.getByText('Resolvidas').closest('div')).toHaveTextContent('2');
  });
});

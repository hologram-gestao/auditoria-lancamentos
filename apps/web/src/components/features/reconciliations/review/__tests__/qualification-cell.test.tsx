/**
 * Testes da célula "Análise" (S19 FRONT 12.2).
 *
 * Cobre os 4 cenários visuais (ok, suspeita, padrao_quebrado, incoerente)
 * e o callback de override em ícones clicáveis. Tooltip body é validado
 * por estrutura (aria-label do trigger) — o conteúdo do Portal do Radix
 * em jsdom é fundamentalmente flaky e tem cobertura E2E em S18.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { QualificationCell } from '@/components/features/reconciliations/review/qualification-cell';
import type { AnomalyItem } from '@/lib/api/reconciliations';

function makeAnomaly(
  overrides: Partial<AnomalyItem> & {
    code?: string;
    context?: string | null;
  },
): AnomalyItem {
  const code = overrides.code ?? 'qualificacao_suspeita';
  return {
    id: `a-${code}`,
    anomaly_type: {
      id: 't-1',
      code,
      name: 'Nome do tipo',
      severity: 'moderate',
    },
    detected_by: 'ai',
    resolved: false,
    context: overrides.context ?? 'Motivo padrão',
    resolution_note: null,
    created_at: '2026-05-26T12:00:00Z',
    related_file_entry: null,
    related_omie_entry: null,
    ...overrides,
  };
}

describe('QualificationCell', () => {
  it('renderiza ícone ok (✅) quando não há anomalias pendentes', () => {
    const onOpenOverride = vi.fn();
    render(<QualificationCell anomalies={[]} onOpenOverride={onOpenOverride} />);

    expect(screen.getByLabelText('Qualificação coerente')).toBeInTheDocument();
    expect(screen.queryByLabelText('Qualificação incoerente')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Qualificação suspeita')).not.toBeInTheDocument();
  });

  it('renderiza ícone âmbar (⚠️) para qualificacao_suspeita', () => {
    render(
      <QualificationCell
        anomalies={[makeAnomaly({ code: 'qualificacao_suspeita' })]}
        onOpenOverride={vi.fn()}
      />,
    );
    const btn = screen.getByRole('button', { name: 'Qualificação suspeita' });
    expect(btn).toBeInTheDocument();
    expect(btn.className).toMatch(/text-amber-700/);
  });

  it('renderiza ícone âmbar (⚠️) para padrao_quebrado e valor_outlier (severity warning)', () => {
    const { rerender } = render(
      <QualificationCell
        anomalies={[makeAnomaly({ code: 'padrao_quebrado' })]}
        onOpenOverride={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Qualificação suspeita' })).toBeInTheDocument();

    rerender(
      <QualificationCell
        anomalies={[makeAnomaly({ code: 'valor_outlier' })]}
        onOpenOverride={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Qualificação suspeita' })).toBeInTheDocument();
  });

  it('renderiza ícone vermelho (❌) quando há qualificacao_incoerente (mesmo com warning ao lado)', () => {
    render(
      <QualificationCell
        anomalies={[
          makeAnomaly({ code: 'qualificacao_suspeita' }),
          makeAnomaly({ code: 'qualificacao_incoerente' }),
        ]}
        onOpenOverride={vi.fn()}
      />,
    );
    const btn = screen.getByRole('button', { name: 'Qualificação incoerente' });
    expect(btn).toBeInTheDocument();
    expect(btn.className).toMatch(/text-red-700/);
  });

  it('dispara onOpenOverride no clique do ícone warning/critical', () => {
    const onOpenOverride = vi.fn();
    render(
      <QualificationCell
        anomalies={[makeAnomaly({ code: 'qualificacao_suspeita' })]}
        onOpenOverride={onOpenOverride}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Qualificação suspeita' }));
    expect(onOpenOverride).toHaveBeenCalledTimes(1);
  });

  it('não renderiza botão clicável quando o estado é ok', () => {
    render(<QualificationCell anomalies={[]} onOpenOverride={vi.fn()} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

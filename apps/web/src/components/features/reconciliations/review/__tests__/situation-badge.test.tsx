/**
 * Testes do badge de situação (FRONT 1.8 — novo estado conciliado_data_divergente).
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SituationBadge } from '@/components/features/reconciliations/review/situation-badge';

describe('SituationBadge', () => {
  it('conciliado_data_divergente → "Data divergente" com tooltip das datas', () => {
    const tip = 'Data no arquivo: 10/04/2026 · Data no Omie: 12/04/2026';
    render(<SituationBadge situation="conciliado_data_divergente" title={tip} />);
    const badge = screen.getByText('Data divergente');
    expect(badge).toBeVisible();
    expect(badge).toHaveAttribute('title', tip);
  });

  it('conciliado → "Conciliado"', () => {
    render(<SituationBadge situation="conciliado" />);
    expect(screen.getByText('Conciliado')).toBeVisible();
  });

  it('sem_omie → "Sem Omie"', () => {
    render(<SituationBadge situation="sem_omie" />);
    expect(screen.getByText('Sem Omie')).toBeVisible();
  });

  it('valor desconhecido → fallback com o texto cru', () => {
    render(<SituationBadge situation="estado_novo" />);
    expect(screen.getByText('estado_novo')).toBeVisible();
  });
});

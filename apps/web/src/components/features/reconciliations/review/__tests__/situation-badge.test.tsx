/**
 * Testes do badge de situação (FRONT 1.8 — novo estado conciliado_data_divergente;
 * 86e2u513n — a dica das datas virou tooltip ACESSÍVEL, não mais `title` nativo).
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { SituationBadge } from '@/components/features/reconciliations/review/situation-badge';

describe('SituationBadge', () => {
  it('conciliado_data_divergente → explicação alcançável por TECLADO e anunciada', async () => {
    const ui = userEvent.setup();
    const tip = 'Data no arquivo: 10/04/2026 · Data no Omie: 12/04/2026';
    render(<SituationBadge situation="conciliado_data_divergente" title={tip} />);

    // O nome acessível carrega a explicação INTEIRA — o leitor de tela anuncia
    // as datas mesmo sem abrir tooltip nenhum.
    const badge = screen.getByRole('img', { name: `Data divergente — ${tip}` });
    expect(badge).toBeVisible();
    // O `title` nativo morreu: não aparecia em toque nem por teclado.
    expect(badge).not.toHaveAttribute('title');

    // Foco de teclado abre a dica (Radix abre no focus).
    await ui.tab();
    expect(badge).toHaveFocus();
    expect(await screen.findByRole('tooltip')).toHaveTextContent(tip);
  });

  it('conciliado_data_divergente SEM title → badge simples, nada focável', () => {
    render(<SituationBadge situation="conciliado_data_divergente" />);
    expect(screen.getByText('Data divergente')).toBeVisible();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
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

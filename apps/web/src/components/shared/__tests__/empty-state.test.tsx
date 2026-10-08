import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { assertNoA11yViolations } from '@/test/a11y';

import { EmptyState } from '../empty-state';
import { ReconciliationsVignette } from '../vignettes';

describe('EmptyState (86e3h57b5)', () => {
  it('moldura tracejada, vinheta decorativa, título e descrição, sem ação', async () => {
    const { container } = render(
      <EmptyState
        data-testid="vazio"
        vignette={<ReconciliationsVignette />}
        title="Nenhuma origem conectada"
        description="Conecte o sistema de onde este cliente traz contas e lançamentos."
      />,
    );
    const vazio = screen.getByTestId('vazio');
    expect(vazio).toHaveClass('border-dashed', 'rounded-lg');
    expect(vazio).not.toHaveAttribute('role');
    expect(screen.getByText('Nenhuma origem conectada')).toHaveClass('font-medium');
    expect(
      screen.getByText('Conecte o sistema de onde este cliente traz contas e lançamentos.'),
    ).toHaveClass('text-muted-foreground');
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    await assertNoA11yViolations(container);
  });

  it('com ação: o slot aparece como veio (quem chama já decidiu a permissão)', () => {
    render(
      <EmptyState
        vignette={<ReconciliationsVignette />}
        description="Nenhuma conciliação."
        action={<button type="button">Criar conciliação</button>}
      />,
    );
    expect(screen.getByRole('button', { name: 'Criar conciliação' })).toBeInTheDocument();
  });

  it('announce liga role="status" (o vazio substitui um resultado carregado)', () => {
    render(
      <EmptyState
        announce
        vignette={<ReconciliationsVignette />}
        description="Nenhuma conciliação encontrada com esses filtros."
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Nenhuma conciliação encontrada com esses filtros.',
    );
  });

  it('sem moldura (dentro de tabela ou card): sem borda e com a vinheta pequena', () => {
    const { container } = render(
      <EmptyState
        data-testid="vazio"
        framed={false}
        vignette={<ReconciliationsVignette />}
        description="Nenhum título nesta carteira."
      />,
    );
    expect(screen.getByTestId('vazio')).not.toHaveClass('border-dashed');
    expect(container.querySelector('svg')?.parentElement).toHaveClass('h-12');
  });
});

/**
 * Totalizador recolhível (86e3fr9qz): começa aberto, recolhe mostrando a linha
 * dos números principais, lembra a escolha POR TELA no navegador e não quebra
 * quando o armazenamento é negado (janela privada, site bloqueado).
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CollapsibleSummary, SummaryInline } from '@/components/shared/collapsible-summary';
import { assertNoA11yViolations } from '@/test/a11y';

function renderSummary(storageKey = 'carteira') {
  return render(
    <CollapsibleSummary
      storageKey={storageKey}
      collapsed={
        <SummaryInline
          items={[
            { key: 'r', value: 'R$ 10,00', label: 'a receber em aberto' },
            { key: 'p', value: 'R$ 5,00', label: 'a pagar em aberto' },
          ]}
        />
      }
      footnote="Clique num valor para filtrar a lista."
    >
      <button type="button">Em aberto: R$ 10,00</button>
    </CollapsibleSummary>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('CollapsibleSummary', () => {
  it('começa aberto: cards e nota visíveis, botão "Ocultar totais" expandido', async () => {
    const { container } = renderSummary();

    expect(screen.getByRole('button', { name: 'Em aberto: R$ 10,00' })).toBeVisible();
    expect(screen.getByText('Clique num valor para filtrar a lista.')).toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: 'Ocultar totais' });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await assertNoA11yViolations(container);
  });

  it('recolhido: some com os cards e mostra a linha com os números principais', async () => {
    const { container } = renderSummary();

    await userEvent.click(screen.getByRole('button', { name: 'Ocultar totais' }));

    expect(screen.queryByRole('button', { name: 'Em aberto: R$ 10,00' })).not.toBeInTheDocument();
    expect(screen.getByTestId('summary-collapsed')).toHaveTextContent(
      'R$ 10,00 a receber em aberto · R$ 5,00 a pagar em aberto',
    );
    expect(screen.getByRole('button', { name: 'Mostrar totais' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    await assertNoA11yViolations(container);
  });

  it('lembra a escolha por tela: outra tela continua aberta', async () => {
    const { unmount } = renderSummary('carteira');
    await userEvent.click(screen.getByRole('button', { name: 'Ocultar totais' }));
    unmount();

    const again = renderSummary('carteira');
    expect(await screen.findByRole('button', { name: 'Mostrar totais' })).toBeInTheDocument();
    again.unmount();

    renderSummary('de-para');
    expect(screen.getByRole('button', { name: 'Ocultar totais' })).toBeInTheDocument();
  });

  it('armazenamento negado: abre aberto e alterna só na memória, sem erro', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    renderSummary();

    expect(screen.getByRole('button', { name: 'Ocultar totais' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Ocultar totais' }));
    expect(screen.getByRole('button', { name: 'Mostrar totais' })).toBeInTheDocument();
  });
});

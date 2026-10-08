/**
 * Contador do `NavLink` (86e3k1q2j): pronto para a subtask 4 preencher. O
 * número nunca vai sozinho para o leitor de tela, e sem rótulo ele não aparece.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { NavLink } from '@/components/features/navigation/nav-link';
import { assertNoA11yViolations } from '@/test/a11y';

describe('NavLink: contador', () => {
  it('sem count não renderiza pílula', () => {
    render(
      <NavLink href="/x" active={false} icon={null}>
        Carteira
      </NavLink>,
    );
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByRole('link')).toHaveTextContent(/^Carteira$/);
  });

  it('com count e rótulo: pílula no tom pedido e nome acessível no link', async () => {
    const { container } = render(
      <NavLink
        href="/x"
        active={false}
        icon={null}
        count={12}
        countTone="destructive"
        countLabel="12 títulos vencidos"
      >
        Carteira
      </NavLink>,
    );
    const pill = screen.getByRole('img', { name: '12 títulos vencidos' });
    expect(pill).toHaveTextContent('12');
    expect(pill).toHaveClass('bg-destructive-muted', 'text-destructive', 'rounded-full');
    expect(screen.getByRole('link')).toHaveAccessibleName('Carteira 12 títulos vencidos');
    await assertNoA11yViolations(container);
  });

  it('tom padrão é info', () => {
    render(
      <NavLink href="/x" active={false} icon={null} count={1} countLabel="1 em revisão">
        Conciliações
      </NavLink>,
    );
    expect(screen.getByRole('img', { name: '1 em revisão' })).toHaveClass(
      'bg-info-muted',
      'text-info',
    );
  });

  it('count sem rótulo não aparece: o número sozinho não diz do que é', () => {
    render(
      <NavLink href="/x" active={false} icon={null} count={3} countTone="warning">
        De-para
      </NavLink>,
    );
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });
});

describe('NavLink: tooltip do contador (ajuste de 08/10/2026)', () => {
  const DETAIL = '12 títulos vencidos: 9 a receber · 3 a pagar';

  function renderComContador() {
    return render(
      <NavLink
        href="/x"
        active={false}
        icon={null}
        count={12}
        countTone="destructive"
        countLabel={DETAIL}
        countDetail={DETAIL}
      >
        Carteira
      </NavLink>,
    );
  }

  it('abre no hover do link e mostra o detalhe', async () => {
    const user = userEvent.setup();
    renderComContador();
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    await user.hover(screen.getByRole('link'));
    expect(await screen.findByRole('tooltip')).toHaveTextContent(DETAIL);
  });

  it('abre no foco do teclado, e a pílula não vira um segundo focável', async () => {
    const user = userEvent.setup();
    renderComContador();
    await user.tab();
    expect(screen.getByRole('link')).toHaveFocus();
    expect(await screen.findByRole('tooltip')).toHaveTextContent(DETAIL);
    expect(screen.getByRole('img', { name: DETAIL })).not.toHaveAttribute('tabindex');
    await user.tab();
    expect(screen.getByRole('link')).not.toHaveFocus();
  });

  it('sem contador, sem tooltip', async () => {
    const user = userEvent.setup();
    render(
      <NavLink href="/x" active={false} icon={null}>
        Painel
      </NavLink>,
    );
    await user.hover(screen.getByRole('link'));
    await user.tab();
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    expect(screen.getByRole('link')).not.toHaveAttribute('data-state');
  });
});

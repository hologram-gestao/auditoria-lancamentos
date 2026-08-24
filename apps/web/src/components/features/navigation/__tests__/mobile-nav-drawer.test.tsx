/**
 * Testes do drawer de navegação mobile (86e2n4pf9).
 *
 * Cobre: hambúrguer com rótulo acessível e restrito a `md:hidden`; drawer
 * renderiza o MESMO `SidebarNav` em camadas (global e cliente); fecha ao
 * clicar num link (`onNavigate`) E ao mudar o pathname (garantia); axe no
 * estado aberto. O trio foco-preso/Esc/foco-de-volta é do Radix e é validado
 * no e2e (browser real), não aqui.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let currentPathname = '/clientes';

vi.mock('next/navigation', () => ({
  usePathname: () => currentPathname,
}));

const detailState = {
  data: undefined as { name: string } | undefined,
  isLoading: false,
  isError: false,
};

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => detailState,
}));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo — importar no topo as avaliaria antes da inicialização).
import { MobileNavDrawer } from '@/components/features/navigation/mobile-nav-drawer';
import type { AuthenticatedUser } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const ADMIN: AuthenticatedUser = {
  id: 'u-admin',
  email: 'admin@hologram.com.br',
  name: 'Admin',
  role: 'admin',
  scope: 'system',
  client_id: null,
};

beforeAll(() => {
  // Shims que o Radix espera e o jsdom não tem.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  currentPathname = '/clientes';
  detailState.data = { name: 'Cliente Exemplo Ltda' };
  detailState.isError = false;
});

describe('MobileNavDrawer', () => {
  it('hambúrguer tem rótulo acessível e só existe abaixo de md', () => {
    render(<MobileNavDrawer user={ADMIN} />);

    const trigger = screen.getByRole('button', { name: 'Abrir menu de navegação' });
    expect(trigger).toHaveClass('md:hidden');
  });

  it('abre com o menu em camadas dentro — camada global fora do cliente', async () => {
    const user = userEvent.setup();
    const { baseElement } = render(<MobileNavDrawer user={ADMIN} />);

    await user.click(screen.getByRole('button', { name: 'Abrir menu de navegação' }));

    const dialog = await screen.findByRole('dialog', { name: 'Menu' });
    const nav = within(dialog).getByRole('navigation', { name: 'Navegação principal' });
    expect(within(nav).getByRole('link', { name: 'Clientes' })).toBeInTheDocument();
    await assertNoA11yViolations(baseElement);
  });

  it('no contexto de cliente o drawer mostra a camada do cliente', async () => {
    currentPathname = '/clientes/c1/contas';
    const user = userEvent.setup();
    render(<MobileNavDrawer user={ADMIN} />);

    await user.click(screen.getByRole('button', { name: 'Abrir menu de navegação' }));

    const dialog = await screen.findByRole('dialog', { name: 'Menu' });
    const nav = within(dialog).getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'Voltar para clientes' })).toBeInTheDocument();
    expect(within(nav).getByText('Cliente Exemplo Ltda')).toBeInTheDocument();
  });

  it('clicar num link fecha o drawer — inclusive o da página atual', async () => {
    const user = userEvent.setup();
    render(<MobileNavDrawer user={ADMIN} />);

    await user.click(screen.getByRole('button', { name: 'Abrir menu de navegação' }));
    const dialog = await screen.findByRole('dialog', { name: 'Menu' });

    // "/clientes" é a página ATUAL do mock: o pathname não muda no clique, e é
    // exatamente o caso que só o `onNavigate` cobre.
    await user.click(within(dialog).getByRole('link', { name: 'Clientes' }));

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('mudança de pathname fecha o drawer (navegação fora dos links dele)', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<MobileNavDrawer user={ADMIN} />);

    await user.click(screen.getByRole('button', { name: 'Abrir menu de navegação' }));
    await screen.findByRole('dialog', { name: 'Menu' });

    currentPathname = '/clientes/c1';
    rerender(<MobileNavDrawer user={ADMIN} />);

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});

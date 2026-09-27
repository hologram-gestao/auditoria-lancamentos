/**
 * Testes do menu "Ações do cliente" do shell (86e3eq9uy, Parte E).
 *
 * Cobre o que o jsdom consegue provar: o gatilho só existe para quem tem
 * `edit_client` e com o cliente aberto; o menu tem SÓ "Editar cliente" e
 * "Encerrar cliente" (nunca "Excluir cliente", 86e3eqxdt); escolher um item
 * fecha o menu e SÓ ENTÃO abre o diálogo correspondente (nunca dois overlays
 * do Radix ao mesmo tempo); e o menu é `modal={false}` — o resto da página não
 * ganha `aria-hidden` enquanto ele está aberto. A devolução do foco ao gatilho
 * depois do diálogo é medida no browser (e2e).
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/clientes/c1/carteira',
}));

const clientState = {
  data: undefined as
    | {
        id: string;
        name: string;
        active: boolean;
        closed_at: string | null;
        accounts: { omie_conta_id: number; name: string }[];
      }
    | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

vi.mock('@/hooks/use-clients', () => ({
  useCloseClient: () => ({ mutateAsync: vi.fn(), isPending: false, reset: vi.fn() }),
  useSetFavorite: () => ({ mutate: vi.fn(), isPending: false }),
  useClientDetail: () => clientState,
}));

vi.mock('@/hooks/use-reconciliations', () => ({
  useSessionDetail: () => ({ data: undefined, isError: false }),
}));

let currentUser: unknown;

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { user: unknown }) => unknown) => selector({ user: currentUser }),
}));

// O modal de edição real puxa formulário, categorias e conexões: um dublê que
// só diz se está aberto basta para provar o encadeamento menu → diálogo.
vi.mock('@/components/features/clients/edit-client-modal', () => ({
  EditClientModal: ({ open }: { open: boolean }) =>
    open ? <div role="dialog" aria-label="Editar cliente (dublê)" /> : null,
}));

import { ClientShell } from '@/components/features/clients/client-shell';
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

const MANAGER: AuthenticatedUser = { ...ADMIN, id: 'u-mgr', role: 'manager' };

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  currentUser = ADMIN;
  clientState.data = {
    id: 'c1',
    name: 'Padaria Pão Quente Ltda',
    active: true,
    closed_at: null,
    accounts: [],
  };
});

function renderShell() {
  return render(
    <ClientShell clientId="c1">
      <div>conteúdo</div>
    </ClientShell>,
  );
}

describe('ClientShell — menu "Ações do cliente" (86e3eq9uy)', () => {
  it('abre com SÓ dois itens, e "Excluir cliente" não existe em forma nenhuma', async () => {
    const user = userEvent.setup();
    const { container } = renderShell();

    // Os botões soltos de antes não existem mais.
    expect(screen.queryByRole('button', { name: 'Editar cliente' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Encerrar cliente' })).toBeNull();

    const gatilho = screen.getByRole('button', { name: 'Ações do cliente' });
    expect(gatilho).toHaveAttribute('aria-haspopup', 'menu');
    await user.click(gatilho);

    const menu = await screen.findByRole('menu');
    const itens = within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.textContent?.trim());
    expect(itens).toEqual(['Editar cliente', 'Encerrar cliente']);
    expect(screen.queryByText('Excluir cliente')).toBeNull();

    // `modal={false}`: o resto da página NÃO fica `aria-hidden` com o menu aberto
    // (o modo modal do Radix esconde o fundo mantendo-o focável: `aria-hidden-focus`).
    expect(screen.getByText('conteúdo').closest('[aria-hidden="true"]')).toBeNull();
    await assertNoA11yViolations(container);
  });

  it('"Encerrar cliente" fecha o menu e só então abre o alertdialog', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: 'Ações do cliente' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Encerrar cliente' }));

    const confirm = await screen.findByRole('alertdialog', { name: 'Encerrar cliente' });
    expect(confirm).toBeVisible();
    // Nunca dois overlays ao mesmo tempo: o menu já saiu quando o diálogo entrou.
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('"Editar cliente" fecha o menu e abre o modal de edição', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: 'Ações do cliente' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Editar cliente' }));

    await waitFor(() =>
      expect(screen.getByRole('dialog', { name: 'Editar cliente (dublê)' })).toBeInTheDocument(),
    );
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('quem não tem edit_client não vê o gatilho (ação oculta, nunca desabilitada)', () => {
    currentUser = MANAGER;
    renderShell();
    expect(screen.queryByRole('button', { name: 'Ações do cliente' })).toBeNull();
    expect(screen.queryByRole('button', { name: /cliente/ })).toBeNull();
  });

  it('cliente encerrado não tem item nenhum, então o gatilho some (menu vazio é defeito)', () => {
    clientState.data = { ...clientState.data!, closed_at: '2026-09-20T12:00:00Z' };
    renderShell();
    expect(screen.queryByRole('button', { name: 'Ações do cliente' })).toBeNull();
    expect(screen.queryByRole('menuitem')).toBeNull();
  });
});

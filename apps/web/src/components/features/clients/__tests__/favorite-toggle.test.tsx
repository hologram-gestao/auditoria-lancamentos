/**
 * Coração de favorito (86e34jd5a): botão de alternância acessível, com nome
 * que carrega o cliente e a ação; clique não navega a linha; erro vira toast
 * com a mensagem do servidor.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mutationState = {
  mutate: vi.fn(),
  isPending: false,
};
vi.mock('@/hooks/use-clients', () => ({
  // O ClientShell agora monta o diálogo de exclusão (86e34jd1d).
  useSetFavorite: () => mutationState,
}));

const toastError = vi.fn();
vi.mock('sonner', () => ({ toast: { error: (...args: unknown[]) => toastError(...args) } }));

import { FavoriteToggle } from '@/components/features/clients/favorite-toggle';
import { ApiError } from '@/lib/api/client';
import { assertNoA11yViolations } from '@/test/a11y';

beforeEach(() => {
  mutationState.mutate.mockReset();
  mutationState.isPending = false;
  toastError.mockReset();
});

describe('FavoriteToggle', () => {
  it('não favoritado: aria-pressed=false e o clique marca (true)', async () => {
    const { container } = render(
      <FavoriteToggle clientId="c1" clientName="Cliente Exemplo" isFavorite={false} />,
    );
    const botao = screen.getByRole('button', { name: 'Favoritar Cliente Exemplo' });
    expect(botao).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(botao);
    expect(mutationState.mutate).toHaveBeenCalledTimes(1);
    expect(mutationState.mutate.mock.calls[0]?.[0]).toBe(true);

    await assertNoA11yViolations(container);
  });

  it('favoritado: aria-pressed=true, nome diz "Remover", clique desmarca (false)', () => {
    render(<FavoriteToggle clientId="c1" clientName="Cliente Exemplo" isFavorite />);
    const botao = screen.getByRole('button', { name: 'Remover Cliente Exemplo dos favoritos' });
    expect(botao).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(botao);
    expect(mutationState.mutate.mock.calls[0]?.[0]).toBe(false);
  });

  it('o clique NÃO propaga para a linha (a linha navega ao ser clicada)', () => {
    const onRowClick = vi.fn();
    render(
      <div onClick={onRowClick} role="presentation">
        <FavoriteToggle clientId="c1" clientName="Cliente Exemplo" isFavorite={false} />
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Favoritar Cliente Exemplo' }));
    expect(mutationState.mutate).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('enquanto salva, o botão fica desabilitado (dois cliques não viram dois PUTs)', () => {
    mutationState.isPending = true;
    render(<FavoriteToggle clientId="c1" clientName="Cliente Exemplo" isFavorite={false} />);
    expect(screen.getByRole('button', { name: 'Favoritar Cliente Exemplo' })).toBeDisabled();
  });

  it('erro do servidor vira toast com a mensagem para o usuário', () => {
    render(<FavoriteToggle clientId="c1" clientName="Cliente Exemplo" isFavorite={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Favoritar Cliente Exemplo' }));

    const options = mutationState.mutate.mock.calls[0]?.[1] as {
      onError: (err: Error) => void;
    };
    options.onError(
      new ApiError(403, {
        code: 'FORBIDDEN',
        message: 'forbidden',
        userMessage: 'Você não tem acesso a este cliente.',
      }),
    );
    expect(toastError).toHaveBeenCalledWith('Você não tem acesso a este cliente.');

    options.onError(new Error('boom'));
    expect(toastError).toHaveBeenLastCalledWith('Não foi possível atualizar o favorito.');
  });
});

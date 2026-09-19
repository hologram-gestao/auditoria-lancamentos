/**
 * Exclusão definitiva (86e34jd1d): confirmação DIGITADA — a ação só libera
 * quando o nome bate; no sucesso, toast e volta para a lista.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

const mutationState = {
  mutateAsync: vi.fn(),
  isPending: false,
  reset: vi.fn(),
};
vi.mock('@/hooks/use-clients', () => ({
  useDeleteClient: () => mutationState,
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

import { DeleteClientDialog } from '@/components/features/clients/delete-client-dialog';
import type { Client } from '@/lib/api/clients';
import { assertNoA11yViolations } from '@/test/a11y';

const client: Client = {
  id: 'c1',
  name: 'Cliente Exemplo Ltda',
  active: true,
  organization: { id: '0706eeb5-9718-4d03-bcda-ef615789e6ac', name: 'Hologram' },
  created_at: '2026-05-01T12:00:00Z',
  updated_at: '2026-07-20T12:00:00Z',
  responsible_manager: null,
  reconciliation_count: 3,
  is_favorite: false,
  manager_count: 1,
  category: null,
};

beforeEach(() => {
  mutationState.mutateAsync.mockReset().mockResolvedValue(undefined);
  mutationState.isPending = false;
  replace.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
});

describe('DeleteClientDialog', () => {
  it('só libera a exclusão quando o nome digitado bate, e volta para a lista', async () => {
    const { baseElement } = render(
      <DeleteClientDialog open onOpenChange={vi.fn()} client={client} />,
    );
    const dialog = screen.getByRole('alertdialog', { name: 'Excluir cliente' });
    expect(dialog).toHaveTextContent('3 conciliações');
    const acao = screen.getByRole('button', { name: 'Excluir definitivamente' });
    expect(acao).toBeDisabled();

    const campo = screen.getByLabelText('Digite o nome do cliente para confirmar');
    fireEvent.change(campo, { target: { value: 'Cliente Exemplo' } });
    expect(acao).toBeDisabled();
    fireEvent.change(campo, { target: { value: '  Cliente Exemplo Ltda ' } });
    expect(acao).toBeEnabled();

    fireEvent.click(acao);
    await waitFor(() => expect(mutationState.mutateAsync).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/clientes'));
    expect(toastSuccess).toHaveBeenCalled();

    await assertNoA11yViolations(baseElement);
  });

  it('erro do servidor vira toast e não navega', async () => {
    mutationState.mutateAsync.mockRejectedValueOnce(new Error('boom'));
    render(<DeleteClientDialog open onOpenChange={vi.fn()} client={client} />);
    fireEvent.change(screen.getByLabelText('Digite o nome do cliente para confirmar'), {
      target: { value: 'Cliente Exemplo Ltda' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Excluir definitivamente' }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Não foi possível excluir o cliente.'),
    );
    expect(replace).not.toHaveBeenCalled();
  });
});

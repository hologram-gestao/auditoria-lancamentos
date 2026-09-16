/**
 * Encerramento com retenção (86e36pm1z): confirmação DIGITADA — a ação só
 * libera quando o nome bate; no sucesso, toast e o diálogo fecha (SEM navegar:
 * o cliente continua existindo, só-leitura).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mutationState = {
  mutateAsync: vi.fn(),
  isPending: false,
  reset: vi.fn(),
};
vi.mock('@/hooks/use-clients', () => ({
  useCloseClient: () => mutationState,
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

import { CloseClientDialog } from '@/components/features/clients/close-client-dialog';
import type { Client } from '@/lib/api/clients';
import { assertNoA11yViolations } from '@/test/a11y';

const client: Client = {
  id: 'c1',
  name: 'Cliente Exemplo Ltda',
  active: true,
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
  toastSuccess.mockReset();
  toastError.mockReset();
});

describe('CloseClientDialog', () => {
  it('só libera o encerramento quando o nome digitado bate, e fecha sem navegar', async () => {
    const onOpenChange = vi.fn();
    const { baseElement } = render(
      <CloseClientDialog open onOpenChange={onOpenChange} client={client} />,
    );
    const dialog = screen.getByRole('alertdialog', { name: 'Encerrar cliente' });
    expect(dialog).toHaveTextContent('3 conciliações');
    expect(dialog).toHaveTextContent('apenas para consulta');
    const acao = screen.getByRole('button', { name: 'Encerrar cliente' });
    expect(acao).toBeDisabled();

    const campo = screen.getByLabelText('Digite o nome do cliente para confirmar');
    fireEvent.change(campo, { target: { value: 'Cliente Exemplo' } });
    expect(acao).toBeDisabled();
    fireEvent.change(campo, { target: { value: '  Cliente Exemplo Ltda ' } });
    expect(acao).toBeEnabled();

    fireEvent.click(acao);
    await waitFor(() => expect(mutationState.mutateAsync).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(toastSuccess).toHaveBeenCalled();

    await assertNoA11yViolations(baseElement);
  });

  it('erro do servidor vira toast e o diálogo continua aberto', async () => {
    mutationState.mutateAsync.mockRejectedValueOnce(new Error('boom'));
    const onOpenChange = vi.fn();
    render(<CloseClientDialog open onOpenChange={onOpenChange} client={client} />);
    fireEvent.change(screen.getByLabelText('Digite o nome do cliente para confirmar'), {
      target: { value: 'Cliente Exemplo Ltda' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Encerrar cliente' }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Não foi possível encerrar o cliente.'),
    );
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});

/**
 * Gaveta de conectar / editar origem (Sprint 9 / R3 · R5).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 *
 * O que este arquivo trava:
 *   - o gate "Testar" → só então "Salvar", nos dois modos;
 *   - as chaves de credencial são `app_key`/`app_secret` (**snake_case**) — a
 *     docstring do schema do backend diz `appKey`/`appSecret` e está errada, e
 *     chave errada é 422 do adaptador;
 *   - rótulo em branco não vai no corpo (o backend usa o padrão do tipo; `""`
 *     seria 422 no `_clean_label`);
 *   - 409 de `(tipo, rótulo)` repetido vira erro INLINE no campo rótulo, com o
 *     nome da origem que ocupa o par — nunca um toast genérico;
 *   - na edição, renomear sem mexer na credencial manda só o rótulo.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/clientes/c1/painel',
  useSearchParams: () => new URLSearchParams(''),
}));

const createMock = vi.fn();
const updateMock = vi.fn();
const testMock = vi.fn();

vi.mock('@/hooks/use-client-connections', () => ({
  useCreateConnection: () => ({ mutateAsync: createMock, isPending: false }),
  useUpdateConnection: () => ({ mutateAsync: updateMock, isPending: false }),
}));

vi.mock('@/hooks/use-clients', () => ({
  useTestConnection: () => ({ mutateAsync: testMock, isPending: false, reset: vi.fn() }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { ConnectionFormDrawer } from '@/components/features/clients/connections/connection-form-drawer';
import { ApiError } from '@/lib/api/client';
import type { ClientConnection } from '@/lib/contracts';

const CLIENT_ID = 'c1';

function connection(over: Partial<ClientConnection> = {}): ClientConnection {
  return {
    id: 'conn-1',
    provider_type: 'omie',
    label: 'Omie matriz',
    status: 'ativa',
    last_checked_at: '2026-09-22T12:00:00Z',
    accounts_synced_at: null,
    capabilities: ['verificar_credencial', 'listar_contas', 'listar_lancamentos', 'escrever'],
    ...over,
  };
}

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  createMock.mockReset().mockResolvedValue(connection());
  updateMock.mockReset().mockResolvedValue(connection());
  testMock.mockReset().mockResolvedValue({ ok: true, message: 'ok' });
});

describe('Conectar origem — gate do teste', () => {
  it('Salvar só habilita depois do teste bem-sucedido', async () => {
    const user = userEvent.setup();
    render(
      <ConnectionFormDrawer
        open
        onOpenChange={vi.fn()}
        clientId={CLIENT_ID}
        connection={null}
        connections={[]}
      />,
    );

    const salvar = screen.getByRole('button', { name: 'Salvar origem' });
    expect(salvar).toBeDisabled();

    await user.type(screen.getByLabelText('App Key Omie'), 'chave');
    await user.type(screen.getByLabelText('App Secret Omie'), 'segredo');
    expect(salvar).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /Testar conexão/ }));
    await waitFor(() => expect(salvar).toBeEnabled());
  });

  it('manda as credenciais em snake_case e omite o rótulo em branco', async () => {
    const user = userEvent.setup();
    render(
      <ConnectionFormDrawer
        open
        onOpenChange={vi.fn()}
        clientId={CLIENT_ID}
        connection={null}
        connections={[]}
      />,
    );

    await user.type(screen.getByLabelText('App Key Omie'), 'chave');
    await user.type(screen.getByLabelText('App Secret Omie'), 'segredo');
    await user.click(screen.getByRole('button', { name: /Testar conexão/ }));
    await user.click(await screen.findByRole('button', { name: 'Salvar origem' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock.mock.calls[0]![0]).toEqual({
      provider_type: 'omie',
      credentials: { app_key: 'chave', app_secret: 'segredo' },
    });
  });

  it('409 de rótulo repetido vira erro INLINE nomeando a origem existente', async () => {
    const user = userEvent.setup();
    const { toast } = await import('sonner');
    const existing = connection({ id: 'conn-existente', label: 'Omie filial' });
    createMock.mockRejectedValue(
      new ApiError(409, {
        code: 'CONFLICT',
        message: 'duplicate',
        userMessage: 'Já existe uma origem deste tipo com este rótulo neste cliente.',
        details: { existingConnectionId: 'conn-existente' },
      }),
    );

    render(
      <ConnectionFormDrawer
        open
        onOpenChange={vi.fn()}
        clientId={CLIENT_ID}
        connection={null}
        connections={[existing]}
      />,
    );

    await user.type(screen.getByLabelText('Rótulo (opcional)'), 'Omie filial');
    await user.type(screen.getByLabelText('App Key Omie'), 'chave');
    await user.type(screen.getByLabelText('App Secret Omie'), 'segredo');
    await user.click(screen.getByRole('button', { name: /Testar conexão/ }));
    await user.click(await screen.findByRole('button', { name: 'Salvar origem' }));

    expect(await screen.findByText(/"Omie filial" já está em uso/)).toBeVisible();
    expect(toast.error).not.toHaveBeenCalled();
  });
});

describe('Editar origem', () => {
  it('renomear sem mexer na credencial manda só o rótulo', async () => {
    const user = userEvent.setup();
    render(
      <ConnectionFormDrawer
        open
        onOpenChange={vi.fn()}
        clientId={CLIENT_ID}
        connection={connection()}
        connections={[connection()]}
      />,
    );

    const label = screen.getByLabelText('Rótulo');
    await user.clear(label);
    await user.type(label, 'Omie principal');
    await user.click(screen.getByRole('button', { name: 'Salvar alterações' }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    expect(updateMock.mock.calls[0]![0]).toEqual({ label: 'Omie principal' });
  });

  it('sem nenhuma mudança o Salvar fica bloqueado (corpo vazio é 422)', () => {
    render(
      <ConnectionFormDrawer
        open
        onOpenChange={vi.fn()}
        clientId={CLIENT_ID}
        connection={connection()}
        connections={[connection()]}
      />,
    );
    expect(screen.getByRole('button', { name: 'Salvar alterações' })).toBeDisabled();
  });

  it('credencial nova exige teste antes de salvar', async () => {
    const user = userEvent.setup();
    render(
      <ConnectionFormDrawer
        open
        onOpenChange={vi.fn()}
        clientId={CLIENT_ID}
        connection={connection()}
        connections={[connection()]}
      />,
    );

    await user.type(screen.getByLabelText('App Key Omie'), 'nova');
    await user.type(screen.getByLabelText('App Secret Omie'), 'nova-secret');
    const salvar = screen.getByRole('button', { name: 'Salvar alterações' });
    expect(salvar).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /Testar conexão/ }));
    await waitFor(() => expect(salvar).toBeEnabled());

    await user.click(salvar);
    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    expect(updateMock.mock.calls[0]![0]).toEqual({
      credentials: { app_key: 'nova', app_secret: 'nova-secret' },
    });
  });
});

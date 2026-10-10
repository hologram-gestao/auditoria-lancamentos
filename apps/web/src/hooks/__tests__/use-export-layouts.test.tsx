/**
 * Hook de exclusão de layout de exportação (86e3nuuub):
 *   - chama o DELETE com o id do layout;
 *   - no sucesso, tira do cache o detalhe do layout excluído (refazê-lo daria
 *     404) e invalida a raiz, para a lista e o seletor da geração do arquivo;
 *   - na recusa (409 `LAYOUT_EM_USO`), nada sai do cache.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const deleteExportLayoutMock = vi.fn();
vi.mock('@/lib/api/export-layouts', () => ({
  createExportLayoutFromTemplate: vi.fn(),
  deleteExportLayout: (...args: unknown[]) => deleteExportLayoutMock(...args),
  getExportLayout: vi.fn(),
  listExportLayouts: vi.fn(),
  listExportLayoutTemplates: vi.fn(),
}));

import { exportLayoutsKeys, useDeleteExportLayout } from '@/hooks/use-export-layouts';
import { ApiError } from '@/lib/api/client';

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function wrapperWith(client: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useDeleteExportLayout', () => {
  it('chama o DELETE, tira o detalhe do cache e invalida a raiz', async () => {
    deleteExportLayoutMock.mockResolvedValue(undefined);
    const client = newClient();
    client.setQueryData(exportLayoutsKeys.detail('lay-1'), { id: 'lay-1' });
    client.setQueryData(exportLayoutsKeys.list(null), [{ id: 'lay-1' }]);
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    const { result } = renderHook(() => useDeleteExportLayout(), {
      wrapper: wrapperWith(client),
    });
    await act(() => result.current.mutateAsync('lay-1'));

    expect(deleteExportLayoutMock).toHaveBeenCalledWith('lay-1', expect.anything());
    expect(client.getQueryData(exportLayoutsKeys.detail('lay-1'))).toBeUndefined();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: exportLayoutsKeys.all });
    expect(client.getQueryState(exportLayoutsKeys.list(null))?.isInvalidated).toBe(true);
  });

  it('na recusa 409 LAYOUT_EM_USO nada sai do cache nem é invalidado', async () => {
    deleteExportLayoutMock.mockRejectedValue(
      new ApiError(409, {
        code: 'LAYOUT_EM_USO',
        message: 'em uso',
        userMessage: 'Este layout já gerou 2 arquivos contábeis.',
      }),
    );
    const client = newClient();
    client.setQueryData(exportLayoutsKeys.detail('lay-1'), { id: 'lay-1' });
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    const { result } = renderHook(() => useDeleteExportLayout(), {
      wrapper: wrapperWith(client),
    });
    await act(async () => {
      await expect(result.current.mutateAsync('lay-1')).rejects.toBeInstanceOf(ApiError);
    });

    expect(client.getQueryData(exportLayoutsKeys.detail('lay-1'))).toEqual({ id: 'lay-1' });
    expect(invalidate).not.toHaveBeenCalled();
  });
});

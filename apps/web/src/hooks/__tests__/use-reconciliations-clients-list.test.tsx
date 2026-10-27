/**
 * 86e3w9v8m: a coluna "Última conciliação" da lista de clientes lê a query da
 * LISTA (`clientsKeys.lists`). Quem muda a última sessão de um cliente (criar,
 * reprocessar, cancelar) invalida essa query junto com a lista de conciliações,
 * senão a lista mostraria o selo ou o link da sessão anterior até o cache expirar.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api/reconciliations', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  reprocessReconciliation: vi.fn(() => Promise.resolve({ session_id: 's1' })),
  cancelReconciliation: vi.fn(() => Promise.resolve()),
}));

import { clientsKeys } from '@/hooks/use-clients';
import { useCancelReconciliation, useReprocessReconciliation } from '@/hooks/use-reconciliations';

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(client, 'invalidateQueries');
  function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return { spy, Wrapper };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('mutações de conciliação invalidam a lista de clientes', () => {
  it('reprocessar', async () => {
    const { spy, Wrapper } = setup();
    const { result } = renderHook(() => useReprocessReconciliation('s1', 'c1'), {
      wrapper: Wrapper,
    });
    await act(() => result.current.mutateAsync());
    expect(spy).toHaveBeenCalledWith({ queryKey: clientsKeys.lists });
  });

  it('cancelar', async () => {
    const { spy, Wrapper } = setup();
    const { result } = renderHook(() => useCancelReconciliation('s1', 'c1'), {
      wrapper: Wrapper,
    });
    await act(() => result.current.mutateAsync());
    expect(spy).toHaveBeenCalledWith({ queryKey: clientsKeys.lists });
  });
});

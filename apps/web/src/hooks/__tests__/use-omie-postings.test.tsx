/**
 * Hooks do lançamento no Omie — o que a 86e3n70qj acrescentou:
 *   - o envio grava, no cache da sessão, o par linha → nº do lançamento, e ele
 *     sobrevive à invalidação da revisão (é o que pinta o selo nas duas abas);
 *   - "Lançar no Omie" espera as categorias antes de abrir a gaveta, com o
 *     carregando exposto, e não abre duas vezes num clique repetido;
 *   - falha das categorias não prende a gaveta fechada.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const listOmieCategoriasMock = vi.fn();
const postOmieLancamentosMock = vi.fn();
vi.mock('@/lib/api/omie-postings', () => ({
  listOmieCategorias: (...args: unknown[]) => listOmieCategoriasMock(...args),
  postOmieLancamentos: (...args: unknown[]) => postOmieLancamentosMock(...args),
}));

import {
  collectPosted,
  omiePostingKeys,
  useOpenLancarDrawer,
  usePostedInSession,
  usePostOmieLancamentos,
} from '@/hooks/use-omie-postings';

function wrapperWith(client: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

const PAYLOAD = {
  lines: [
    {
      file_entry_id: 'e1',
      status: 'lancada',
      reason: null,
      message: null,
      omie_lancamento_id: 5001,
    },
    {
      file_entry_id: 'e2',
      status: 'bloqueada',
      reason: 'ja_lancada',
      message: null,
      omie_lancamento_id: 4999,
    },
    {
      file_entry_id: 'e3',
      status: 'erro',
      reason: 'erro_omie',
      message: 'x',
      omie_lancamento_id: null,
    },
  ],
  lancadas: 1,
  bloqueadas: 1,
  com_erro: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('collectPosted', () => {
  it('lançada e já-lançada entram com o número; erro não entra', () => {
    expect(collectPosted(PAYLOAD as never)).toEqual({ e1: 5001, e2: 4999 });
  });
});

describe('usePostOmieLancamentos + usePostedInSession', () => {
  it('o envio grava o par no cache da sessão, e a invalidação da revisão não o apaga', async () => {
    postOmieLancamentosMock.mockResolvedValue(PAYLOAD);
    const client = newClient();
    const { result } = renderHook(
      () => ({ post: usePostOmieLancamentos('s1'), posted: usePostedInSession('s1') }),
      { wrapper: wrapperWith(client) },
    );
    expect(result.current.posted).toEqual({});

    await act(async () => {
      await result.current.post.mutateAsync([{ file_entry_id: 'e1', cod_categoria: '2.01.03' }]);
    });

    await waitFor(() => expect(result.current.posted).toEqual({ e1: 5001, e2: 4999 }));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['review', 's1'] });
      await client.invalidateQueries({ queryKey: ['reconciliations', 's1'] });
    });
    expect(client.getQueryData(omiePostingKeys.postedInSession('s1'))).toEqual({
      e1: 5001,
      e2: 4999,
    });
  });

  it('o estado é por sessão', async () => {
    postOmieLancamentosMock.mockResolvedValue(PAYLOAD);
    const client = newClient();
    const { result } = renderHook(
      () => ({ post: usePostOmieLancamentos('s1'), other: usePostedInSession('s2') }),
      { wrapper: wrapperWith(client) },
    );
    await act(async () => {
      await result.current.post.mutateAsync([{ file_entry_id: 'e1', cod_categoria: '2.01.03' }]);
    });
    expect(result.current.other).toEqual({});
  });
});

describe('useOpenLancarDrawer', () => {
  it('mostra o carregando até as categorias chegarem, e só então abre', async () => {
    let resolve: (value: unknown) => void = () => undefined;
    listOmieCategoriasMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const onReady = vi.fn();
    const { result } = renderHook(() => useOpenLancarDrawer<string>('s1', onReady), {
      wrapper: wrapperWith(newClient()),
    });

    let pending: Promise<void> = Promise.resolve();
    act(() => {
      pending = result.current.open(['e1']);
    });
    await waitFor(() => expect(result.current.openingTargets).toEqual(['e1']));
    expect(onReady).not.toHaveBeenCalled();

    // Clique repetido enquanto carrega: não abre duas vezes.
    act(() => {
      void result.current.open(['e1']);
    });

    await act(async () => {
      resolve({ data: [], total: 0 });
      await pending;
    });
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(onReady).toHaveBeenCalledWith(['e1']);
    expect(result.current.openingTargets).toBeNull();
    expect(listOmieCategoriasMock).toHaveBeenCalledTimes(1);
  });

  it('com as categorias em cache, abre sem ir ao servidor', async () => {
    const client = newClient();
    client.setQueryData(omiePostingKeys.categorias('s1'), { data: [], total: 0 });
    const onReady = vi.fn();
    const { result } = renderHook(() => useOpenLancarDrawer<string>('s1', onReady), {
      wrapper: wrapperWith(client),
    });
    await act(async () => {
      await result.current.open(['e1']);
    });
    expect(listOmieCategoriasMock).not.toHaveBeenCalled();
    expect(onReady).toHaveBeenCalledWith(['e1']);
  });

  it('falha das categorias não prende a gaveta: ela abre e mostra o próprio erro', async () => {
    listOmieCategoriasMock.mockRejectedValue(new Error('omie fora'));
    const onReady = vi.fn();
    const { result } = renderHook(() => useOpenLancarDrawer<string>('s1', onReady), {
      wrapper: wrapperWith(newClient()),
    });
    await act(async () => {
      await result.current.open(['e1']);
    });
    expect(onReady).toHaveBeenCalledWith(['e1']);
    expect(result.current.openingTargets).toBeNull();
  });
});

/**
 * Hooks do lançamento no Omie — o que a 86e3n70qj acrescentou:
 *   - o envio grava, no cache da sessão, a linha → estado concluído (na forma do
 *     `omie_posting` da listagem), e ele sobrevive à invalidação da revisão: é a
 *     ponte até o refetch, quando o selo passa a vir do servidor (86e3n70q0);
 *   - `pickPostingState`: o servidor confirmado vence; o cache só preenche o vão;
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
  pickPostingState,
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

const E1 = expect.objectContaining({
  kind: 'direto',
  status: 'confirmed',
  omie_lancamento_id: 5001,
});
const E2 = expect.objectContaining({ status: 'confirmed', omie_lancamento_id: 4999 });

describe('collectPosted', () => {
  it('lançada e já-lançada entram com o número; erro não entra', () => {
    expect(collectPosted(PAYLOAD as never)).toEqual({ e1: E1, e2: E2 });
  });

  it('provisão entra com o título e a baixa', () => {
    const posted = collectPosted({
      lines: [
        {
          file_entry_id: 'p1',
          status: 'lancada',
          kind: 'provisao',
          step: 'concluido',
          supplier_code: 7,
          payable_id: 88,
          payment_id: 99,
        },
      ],
      lancadas: 1,
      bloqueadas: 0,
      com_erro: 0,
    });
    expect(posted.p1).toEqual({
      kind: 'provisao',
      status: 'confirmed',
      step: 'concluido',
      attempts: 0,
      supplier_code: 7,
      payable_id: 88,
      payment_id: 99,
      omie_lancamento_id: null,
      error_code: null,
      error_message: null,
    });
  });
});

describe('pickPostingState', () => {
  const confirmed = collectPosted(PAYLOAD as never).e1;
  it('o servidor confirmado vence o cache', () => {
    const server = { ...confirmed!, omie_lancamento_id: 1 };
    expect(pickPostingState(server, confirmed)).toBe(server);
  });
  it('o cache preenche o vão até o refetch', () => {
    const server = { ...confirmed!, status: 'pending' };
    expect(pickPostingState(server, confirmed)).toBe(confirmed);
    expect(pickPostingState(null, confirmed)).toBe(confirmed);
  });
  it('sem nada, nada', () => {
    expect(pickPostingState(undefined, undefined)).toBeNull();
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
      await result.current.post.mutateAsync([
        { file_entry_id: 'e1', cod_categoria: '2.01.03', modo: 'direto' },
      ]);
    });

    await waitFor(() => expect(result.current.posted).toEqual({ e1: E1, e2: E2 }));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['review', 's1'] });
      await client.invalidateQueries({ queryKey: ['reconciliations', 's1'] });
    });
    expect(client.getQueryData(omiePostingKeys.postedInSession('s1'))).toEqual({
      e1: E1,
      e2: E2,
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
      await result.current.post.mutateAsync([
        { file_entry_id: 'e1', cod_categoria: '2.01.03', modo: 'direto' },
      ]);
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

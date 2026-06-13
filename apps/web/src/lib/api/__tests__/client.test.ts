import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiGet } from '../client';

/**
 * Mock mínimo de Response — evita depender do `Response` global do jsdom, que
 * é instável entre versões. `rawFetch` só usa `ok`, `status`, `json()` e
 * `headers.get()`.
 */
function mockRes(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: { get: () => null },
  } as unknown as Response;
}

const tokenExpired401 = () =>
  mockRes(401, {
    error: { code: 'TOKEN_EXPIRED', message: 'expired', userMessage: 'Sessão expirou.' },
  });

describe('rawFetch — resiliência do refresh em 401 TOKEN_EXPIRED', () => {
  let assignSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    assignSpy = vi.fn();
    // redirectToLogin() lê window.location.pathname e chama .assign().
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { pathname: '/clientes', assign: assignSpy },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renova e repete a request quando o refresh devolve 200', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(tokenExpired401()) // request original → 401
      .mockResolvedValueOnce(mockRes(200, {})) // POST /auth/refresh → ok
      .mockResolvedValueOnce(mockRes(200, { data: { ok: true } })); // retry da original
    vi.stubGlobal('fetch', fetchMock);

    const result = await apiGet<{ ok: boolean }>('/api/v1/clients');

    expect(result).toEqual({ ok: true });
    expect(assignSpy).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('NÃO desloga quando o refresh falha por 5xx (transitório)', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(tokenExpired401()) // original → 401
      .mockResolvedValueOnce(mockRes(503, {})); // /auth/refresh → 503
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiGet('/api/v1/clients')).rejects.toBeInstanceOf(ApiError);
    // O coração do fix: soluço do servidor não pode derrubar sessão válida.
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it('NÃO desloga quando o refresh falha por erro de rede (transitório)', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(tokenExpired401()) // original → 401
      .mockRejectedValueOnce(new TypeError('network down')); // /auth/refresh → rede
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiGet('/api/v1/clients')).rejects.toBeInstanceOf(ApiError);
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it('desloga quando o refresh é definitivamente rejeitado (401)', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(tokenExpired401()) // original → 401
      .mockResolvedValueOnce(mockRes(401, {})); // /auth/refresh → 401 (refresh inválido)
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiGet('/api/v1/clients')).rejects.toBeInstanceOf(ApiError);
    expect(assignSpy).toHaveBeenCalledWith('/login');
  });
});

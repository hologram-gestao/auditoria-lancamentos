/**
 * Wrapper de fetch para a API do backend.
 *
 * Princípios:
 *   - Cookies HttpOnly carregam access/refresh — `credentials: "include"` SEMPRE.
 *   - Em 401 com `code = TOKEN_EXPIRED`, chama `/api/v1/auth/refresh` UMA vez
 *     e repete a request original. Refresh em flight é deduplicado (uma única
 *     promise para múltiplas requests concorrentes que receberam 401).
 *   - Se refresh também falha → redireciona para `/login` no browser.
 *   - Sucesso → retorna `data` desempacotado de `{ data: ... }`.
 *   - Erro → lança `ApiError` com `code`, `userMessage`, `status`.
 *
 * Não usar `localStorage`/`sessionStorage` para tokens — cookies HttpOnly bastam.
 */

const BASE_URL = process.env['NEXT_PUBLIC_API_URL']?.replace(/\/$/, '') ?? 'http://localhost:8000';

const API_PREFIX = '/api/v1';
const REFRESH_PATH = `${API_PREFIX}/auth/refresh`;
const LOGIN_PATH = '/login';

export interface ApiErrorBody {
  code: string;
  message: string;
  userMessage: string;
}

export class ApiError extends Error {
  readonly code: string;
  readonly userMessage: string;
  readonly status: number;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiError';
    this.code = body.code;
    this.userMessage = body.userMessage;
    this.status = status;
  }
}

/**
 * Lançado quando a request falha antes de receber response (offline, DNS, CORS).
 * Mensagem em PT-BR é definida pelos consumidores (ex: tela de login).
 */
export class NetworkError extends Error {
  readonly userMessage = 'Não foi possível conectar ao servidor. Verifique sua conexão.';
  constructor(cause: unknown) {
    super('Network error', { cause });
    this.name = 'NetworkError';
  }
}

type ApiSuccess<T> = { data: T };

interface FetchOptions {
  /** Quando true, NÃO tenta o fluxo de refresh em 401 (uso interno do interceptor). */
  skipRefresh?: boolean;
  /** Headers adicionais — `Content-Type: application/json` é setado por padrão para POST/PATCH/PUT. */
  headers?: Record<string, string>;
  /** AbortSignal opcional — propagado para fetch. */
  signal?: AbortSignal;
}

/** Promise de refresh em andamento — deduplica retries paralelos. */
let inflightRefresh: Promise<boolean> | null = null;

async function performRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}${REFRESH_PATH}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    });
    return res.ok;
  } catch {
    return false;
  }
}

function refreshOnce(): Promise<boolean> {
  if (inflightRefresh === null) {
    inflightRefresh = performRefresh().finally(() => {
      inflightRefresh = null;
    });
  }
  return inflightRefresh;
}

function redirectToLogin(): void {
  if (typeof window !== 'undefined' && window.location.pathname !== LOGIN_PATH) {
    window.location.assign(LOGIN_PATH);
  }
}

async function parseErrorBody(res: Response): Promise<ApiErrorBody> {
  try {
    const json = (await res.json()) as { error?: ApiErrorBody };
    if (json.error?.code && json.error.userMessage) {
      return json.error;
    }
  } catch {
    // ignora — fallback abaixo
  }
  return {
    code: 'UNKNOWN',
    message: `HTTP ${res.status}`,
    userMessage: 'Ocorreu um erro inesperado. Tente novamente.',
  };
}

async function rawFetch<T>(path: string, init: RequestInit, options: FetchOptions): Promise<T> {
  const url = path.startsWith('http') ? path : `${BASE_URL}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        ...(init.body !== undefined && init.body !== null
          ? { 'Content-Type': 'application/json' }
          : {}),
        ...options.headers,
      },
      signal: options.signal,
    });
  } catch (err) {
    throw new NetworkError(err);
  }

  if (res.status === 401 && !options.skipRefresh) {
    const body = await parseErrorBody(res);
    if (body.code === 'TOKEN_EXPIRED') {
      const renewed = await refreshOnce();
      if (renewed) {
        return rawFetch<T>(path, init, { ...options, skipRefresh: true });
      }
      redirectToLogin();
    }
    throw new ApiError(res.status, body);
  }

  if (!res.ok) {
    const body = await parseErrorBody(res);
    throw new ApiError(res.status, body);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const json = (await res.json()) as ApiSuccess<T> | T;
  // Auto-unwrap apenas quando `data` é a ÚNICA chave do envelope.
  // Respostas paginadas (`{ data, pagination }`) e payloads que coincidentemente
  // tenham um campo `data` ficam intactas — caller decide como ler.
  if (
    json !== null &&
    typeof json === 'object' &&
    !Array.isArray(json) &&
    Object.keys(json).length === 1 &&
    'data' in json
  ) {
    return (json as ApiSuccess<T>).data;
  }
  return json as T;
}

export async function apiGet<T>(path: string, options: FetchOptions = {}): Promise<T> {
  return rawFetch<T>(path, { method: 'GET' }, options);
}

export async function apiPost<T>(
  path: string,
  body?: unknown,
  options: FetchOptions = {},
): Promise<T> {
  return rawFetch<T>(
    path,
    {
      method: 'POST',
      body: body === undefined ? null : JSON.stringify(body),
    },
    options,
  );
}

export async function apiPatch<T>(
  path: string,
  body: unknown,
  options: FetchOptions = {},
): Promise<T> {
  return rawFetch<T>(path, { method: 'PATCH', body: JSON.stringify(body) }, options);
}

export async function apiDelete<T>(path: string, options: FetchOptions = {}): Promise<T> {
  return rawFetch<T>(path, { method: 'DELETE' }, options);
}

export const apiBaseUrl = BASE_URL;

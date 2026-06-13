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

// URLs relativas — Next age como reverse proxy via `rewrites()` em
// `next.config.mjs`, encaminhando `/api/v1/*` pro backend server-side.
// Browser nunca toca o domínio real da API → cookie HttpOnly fica na mesma
// origem do Web (resolve cross-site PSL em *.run.app sem custom domain).
const BASE_URL = '';

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

/**
 * Resultado de uma tentativa de refresh:
 *   - `renewed`   → backend emitiu novo par de cookies (res.ok).
 *   - `invalid`   → refresh token ausente/expirado/inválido (401). Sessão acabou
 *                   de verdade; cabe redirecionar para /login.
 *   - `transient` → falha de rede ou 5xx do servidor (cold start, deploy, blip).
 *                   NÃO desloga — a sessão provavelmente ainda é válida; o caller
 *                   deixa o erro borbulhar para retry, sem mandar pro /login.
 */
type RefreshOutcome = 'renewed' | 'invalid' | 'transient';

/** Promise de refresh em andamento — deduplica retries paralelos. */
let inflightRefresh: Promise<RefreshOutcome> | null = null;

async function performRefresh(): Promise<RefreshOutcome> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${REFRESH_PATH}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    });
  } catch {
    // Offline / DNS / conexão cortada — transitório, nunca um logout.
    return 'transient';
  }
  if (res.ok) {
    return 'renewed';
  }
  // SÓ um 401 explícito significa "refresh token não vale mais" → logout real.
  // 5xx (502/503 de deploy, cold start) e demais status são soluços do servidor:
  // deslogar aí derruba uma sessão válida (root cause do bug de logout intermitente).
  return res.status === 401 ? 'invalid' : 'transient';
}

function refreshOnce(): Promise<RefreshOutcome> {
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

function buildHeaders(
  body: BodyInit | null | undefined,
  extra: Record<string, string> | undefined,
): Record<string, string> {
  // Para FormData, deixa o browser definir `Content-Type: multipart/form-data`
  // com a boundary correta. Setar manualmente quebra o parser do FastAPI.
  const isMultipart = typeof FormData !== 'undefined' && body instanceof FormData;
  const hasJsonBody = body !== undefined && body !== null && !isMultipart;
  return {
    Accept: 'application/json',
    ...(hasJsonBody ? { 'Content-Type': 'application/json' } : {}),
    ...extra,
  };
}

async function rawFetch<T>(path: string, init: RequestInit, options: FetchOptions): Promise<T> {
  const url = path.startsWith('http') ? path : `${BASE_URL}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      credentials: 'include',
      headers: buildHeaders(init.body, options.headers),
      signal: options.signal,
    });
  } catch (err) {
    throw new NetworkError(err);
  }

  if (res.status === 401 && !options.skipRefresh) {
    const body = await parseErrorBody(res);
    if (body.code === 'TOKEN_EXPIRED') {
      const outcome = await refreshOnce();
      if (outcome === 'renewed') {
        return rawFetch<T>(path, init, { ...options, skipRefresh: true });
      }
      // Só redireciona quando o refresh foi DEFINITIVAMENTE rejeitado (401).
      // Em falha transitória (5xx/rede) mantém o usuário logado e deixa o erro
      // subir para retry — não força /login num soluço do servidor.
      if (outcome === 'invalid') {
        redirectToLogin();
      }
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

/**
 * POST com `multipart/form-data` para uploads de arquivo.
 *
 * NÃO seta `Content-Type` propositalmente — o browser injeta o
 * `multipart/form-data; boundary=...` correto a partir do `FormData`. Setar
 * manualmente quebra o parsing no servidor (boundary ausente).
 *
 * Demais semânticas (cookie HttpOnly, refresh em 401, desempacotamento de
 * `{data}`, `ApiError`) são compartilhadas com `apiPost` via `rawFetch`.
 */
export async function apiPostMultipart<T>(
  path: string,
  body: FormData,
  options: FetchOptions = {},
): Promise<T> {
  return rawFetch<T>(path, { method: 'POST', body }, options);
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

/**
 * Resposta binária com filename extraído do header (Content-Disposition).
 *
 * Usada por downloads de relatório (S14 Excel) onde o caller precisa do
 * blob + nome do arquivo sugerido pelo backend. Filename ASCII vem do
 * cabeçalho `filename="..."`; se ausente, devolve `null` (caller decide
 * fallback).
 */
export interface BlobResponse {
  blob: Blob;
  filename: string | null;
}

/**
 * Extrai o `filename` ASCII do `Content-Disposition` (RFC 6266).
 *
 * Regra: pega o que está entre aspas após `filename=`. Ignora `filename*`
 * (versão UTF-8 RFC 5987) por dois motivos:
 *   - o backend já sanitiza o ASCII pra ser equivalente (sem acentos);
 *   - decodificar `filename*=UTF-8''...` adiciona superfície de bug por
 *     baixo ganho.
 */
function parseFilenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const match = /filename="([^"]+)"/.exec(header);
  return match?.[1] ?? null;
}

/**
 * POST que devolve binário + filename — base para downloads (Excel, PDF).
 *
 * Reusa o fluxo de auth (cookies, refresh em 401, redirect em login expirado)
 * e a desserialização de `ApiError` no `{ error: {...} }` padrão. Diferença
 * do `apiPost`: nunca tenta `.json()` no caso de sucesso; devolve um
 * `Blob` cru pra que o caller chame `URL.createObjectURL` e dispare o
 * download nativo do browser.
 */
export async function apiPostBlob(
  path: string,
  body?: unknown,
  options: FetchOptions = {},
): Promise<BlobResponse> {
  const url = path.startsWith('http') ? path : `${BASE_URL}${path}`;
  const init: RequestInit = {
    method: 'POST',
    body: body === undefined ? null : JSON.stringify(body),
  };

  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      credentials: 'include',
      headers: buildHeaders(init.body, options.headers),
      signal: options.signal,
    });
  } catch (err) {
    throw new NetworkError(err);
  }

  if (res.status === 401 && !options.skipRefresh) {
    const errBody = await parseErrorBody(res);
    if (errBody.code === 'TOKEN_EXPIRED') {
      const outcome = await refreshOnce();
      if (outcome === 'renewed') {
        return apiPostBlob(path, body, { ...options, skipRefresh: true });
      }
      if (outcome === 'invalid') {
        redirectToLogin();
      }
    }
    throw new ApiError(res.status, errBody);
  }

  if (!res.ok) {
    const errBody = await parseErrorBody(res);
    throw new ApiError(res.status, errBody);
  }

  const blob = await res.blob();
  const filename = parseFilenameFromContentDisposition(res.headers.get('Content-Disposition'));
  return { blob, filename };
}

export const apiBaseUrl = BASE_URL;

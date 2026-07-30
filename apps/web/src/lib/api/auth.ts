/**
 * Calls de autenticação. Espelha o contrato de `apps/api/app/modules/auth/schemas.py`.
 *
 * Sprint 5 (R2): `AuthenticatedUser`/`UserRole` deixaram de ser redigitados aqui
 * e passaram a ser **aliases do contrato gerado**. O payload da sessão ganhou
 * `scope` e `client_id` — mantendo a interface à mão, o front continuaria cego
 * ao tenant do usuário e o `tsc` não acusaria nada.
 */
import type { AuthenticatedUser, UserRole } from '@/lib/contracts';

import { apiPost } from './client';

export type { AuthenticatedUser, UserRole };

interface LoginPayload {
  email: string;
  password: string;
}

interface LoginResponse {
  user: AuthenticatedUser;
}

interface LogoutResponse {
  success: boolean;
}

interface RefreshResponse {
  user: AuthenticatedUser;
}

export async function login(payload: LoginPayload): Promise<AuthenticatedUser> {
  // Login NUNCA passa pelo refresh interceptor (skipRefresh) — 401 aqui é credencial inválida.
  const res = await apiPost<LoginResponse>('/api/v1/auth/login', payload, {
    skipRefresh: true,
  });
  return res.user;
}

export async function logout(): Promise<void> {
  await apiPost<LogoutResponse>('/api/v1/auth/logout', undefined, { skipRefresh: true });
}

/**
 * Repopula a sessão após F5 (Zustand é volátil; cookies HttpOnly persistem).
 * Não passa pelo refresh interceptor (skipRefresh) — falha aqui = sessão expirou.
 */
export async function refreshSession(): Promise<AuthenticatedUser> {
  const res = await apiPost<RefreshResponse>('/api/v1/auth/refresh', undefined, {
    skipRefresh: true,
  });
  return res.user;
}

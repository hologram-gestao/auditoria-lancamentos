/**
 * Calls de autenticação. Espelha o contrato de `apps/api/app/modules/auth/schemas.py`.
 */
import { apiPost } from './client';

export type UserRole = 'admin' | 'manager';

export interface AuthenticatedUser {
  id: string;
  email: string;
  name: string;
  role: UserRole;
}

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

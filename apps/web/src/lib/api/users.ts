/**
 * Helpers tipados do módulo users — admin-only (S4 backend).
 *
 * Espelha `apps/api/app/modules/users/{routes,schemas}.py`. Convenções:
 *   - Paginação responde com aliases camelCase (`pageSize`, `totalPages`).
 *   - PATCH é parcial: campos não enviados não são alterados.
 *   - 409 com `code = CONFLICT` na criação/edição com email duplicado;
 *     `userMessage` já vem em PT-BR ("Este e-mail já está em uso.").
 */
import { apiGet, apiPatch, apiPost } from './client';

export type UserRoleValue = 'admin' | 'manager';

export interface User {
  id: string;
  name: string;
  email: string;
  role: UserRoleValue;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Pagination {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}

export interface UserListResponse {
  data: User[];
  pagination: Pagination;
}

export interface ListUsersParams {
  page?: number;
  pageSize?: number;
  search?: string;
}

export interface CreateUserPayload {
  name: string;
  email: string;
  password: string;
  role: UserRoleValue;
}

export interface UpdateUserPayload {
  name?: string;
  email?: string;
  role?: UserRoleValue;
}

function buildQuery(params: ListUsersParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  const search = params.search?.trim();
  if (search) sp.set('search', search);
  return sp.toString();
}

/**
 * Lista paginada. Os helpers `apiGet/apiPost` desempacotam `{ data: ... }`
 * em respostas que usam esse envelope; aqui o backend devolve `{ data, pagination }`,
 * então pedimos o objeto inteiro como tipo (ele só desempacota se houver
 * APENAS a chave `data` — preservamos `pagination` desambiguando o tipo no consumo).
 */
export async function listUsers(params: ListUsersParams = {}): Promise<UserListResponse> {
  return apiGet<UserListResponse>(`/api/v1/users?${buildQuery(params)}`);
}

export async function createUser(payload: CreateUserPayload): Promise<User> {
  return apiPost<User>('/api/v1/users', payload);
}

export async function getUser(id: string): Promise<User> {
  return apiGet<User>(`/api/v1/users/${id}`);
}

export async function updateUser(id: string, payload: UpdateUserPayload): Promise<User> {
  return apiPatch<User>(`/api/v1/users/${id}`, payload);
}

export async function activateUser(id: string): Promise<User> {
  return apiPost<User>(`/api/v1/users/${id}/activate`);
}

export async function deactivateUser(id: string): Promise<User> {
  return apiPost<User>(`/api/v1/users/${id}/deactivate`);
}

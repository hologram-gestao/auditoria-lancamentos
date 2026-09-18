/**
 * Helpers tipados do módulo users — admin-only (S4 backend).
 *
 * Espelha `apps/api/app/modules/users/{routes,schemas}.py`. Convenções:
 *   - Paginação responde com aliases camelCase (`pageSize`, `totalPages`).
 *   - PATCH é parcial: campos não enviados não são alterados.
 *   - 409 com `code = CONFLICT` na criação/edição com email duplicado;
 *     `userMessage` já vem em PT-BR ("Este e-mail já está em uso.").
 */
import type { SystemUserRole, UserResponse } from '@/lib/contracts';

import { apiGet, apiPatch, apiPost } from './client';

/**
 * Papel aceito nesta API — vem do CONTRATO (`SystemUserRole`), não redigitado
 * (86e36ecwa). `platform_admin` não está no union de propósito: ele nasce só
 * por script no backend e nenhum endpoint o aceita; oferecê-lo num formulário
 * daria 422. Se o backend mudar a whitelist, o `tsc` acusa aqui.
 */
export type UserRoleValue = SystemUserRole;

/**
 * Um staff de organização, como o backend devolve (86e36ed1d).
 *
 * Vem do CONTRATO (`UserResponse`), com um único estreitamento: o `role`, que o
 * OpenAPI expõe como `string` livre. O estreitamento é seguro porque
 * `_staff_select` (users/repository.py) filtra `scope = 'system'` — esta
 * listagem nunca devolve usuário de plataforma nem de cliente. Derivar do
 * contrato em vez de redigitar significa que campo novo no backend chega aqui
 * sozinho; foi assim que `scope`/`organization_id`/`organization_name`
 * entraram.
 */
export type User = Omit<UserResponse, 'role'> & { role: UserRoleValue };

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
  /**
   * Filtro por organização (86e36ed1d). A plataforma escolhe qualquer uma; o
   * staff só a própria (outra é 403, não uma lista vazia).
   */
  organizationId?: string;
  /** Filtro por papel — usado pelo seletor de gerentes da carteira. */
  role?: UserRoleValue;
}

export interface CreateUserPayload {
  name: string;
  email: string;
  password: string;
  role: UserRoleValue;
  /**
   * Organização de destino (86e36ed1d): **obrigatória** para a plataforma,
   * ausente para o admin de organização (o backend usa a da LINHA dele).
   */
  organization_id?: string;
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
  if (params.organizationId) sp.set('organizationId', params.organizationId);
  if (params.role) sp.set('role', params.role);
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

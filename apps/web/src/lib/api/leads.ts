/**
 * POST público do formulário de contato da landing (86e3fr9ut / 86e3fr9vz).
 *
 * Reusa o `apiPost` pelo parser de envelope de erro (`ApiError.userMessage`,
 * `NetworkError`), com `skipRefresh: true`: a rota não tem autenticação, e o
 * visitante NUNCA pode ser mandado para `/login` por um 401 que ela nem devolve.
 */
import type { LeadCreate, LeadReceived } from '@/lib/contracts';

import { apiPost } from './client';

export type { LeadCreate, LeadReceived };

export const LEADS_PATH = '/api/v1/leads';

export function submitLead(payload: LeadCreate): Promise<LeadReceived> {
  return apiPost<LeadReceived>(LEADS_PATH, payload, { skipRefresh: true });
}

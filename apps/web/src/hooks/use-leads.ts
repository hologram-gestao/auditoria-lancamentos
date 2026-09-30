/**
 * Mutação do formulário de contato da landing (86e3fr9vz). Sem cache nem
 * invalidação: nada na tela lê leads.
 */
import { useMutation } from '@tanstack/react-query';

import { submitLead, type LeadCreate, type LeadReceived } from '@/lib/api/leads';

export function useSubmitLead() {
  return useMutation<LeadReceived, Error, LeadCreate>({ mutationFn: submitLead });
}

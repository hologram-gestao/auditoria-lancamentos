/**
 * Rótulo canônico de uma conciliação — "Conta · Mês" (86e2u513w).
 *
 * Fonte ÚNICA do formato: o h1 do detalhe (`session-detail-screen`) e o
 * breadcrumb do `ClientShell` derivam DAQUI. A conta vem da lista sincronizada
 * do cliente (`ClientDetail.accounts`); sessão cuja conta saiu da lista (ex.:
 * removida no Omie) ainda ganha rótulo — o `#id` é o que o usuário consegue
 * conferir do outro lado.
 */
import { formatReferenceMonth } from '@/lib/format';

interface AccountRef {
  omie_conta_id: number;
  name: string;
}

interface SessionRef {
  omie_conta_id: number;
  reference_month: string;
}

export function accountNameFor(accounts: readonly AccountRef[], omieContaId: number): string {
  const account = accounts.find((a) => a.omie_conta_id === omieContaId);
  return account?.name ?? `Conta #${omieContaId}`;
}

export function sessionCrumbLabel(session: SessionRef, accounts: readonly AccountRef[]): string {
  return `${accountNameFor(accounts, session.omie_conta_id)} · ${formatReferenceMonth(session.reference_month)}`;
}

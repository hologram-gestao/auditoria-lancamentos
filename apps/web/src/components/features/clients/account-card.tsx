/**
 * Card de uma conta bancária Omie — Doc §10.1.
 *
 * `account_type` é tratado como string (memória `feedback_pydantic`):
 * se o Omie introduzir um tipo novo, exibimos o valor cru em vez de quebrar.
 *
 * Mapeamento (auditoria M-1, corrigido em 20/05/2026): a v1 deste arquivo
 * dizia `'CA' = Cartão de Crédito`, mas na Omie:
 *   - `CA` = Conta Aplicação (investimento)
 *   - `CR` = Cartão de Crédito
 * Doc oficial declara mais 11 tipos (`AC, AD, CC, CE, CG, CN, CP, CV, CX,
 * MT, PG`) — exibimos os mais comuns com label legível e demais como código.
 */

import { Banknote, CreditCard, PiggyBank, type LucideIcon } from 'lucide-react';

import type { BankAccount } from '@/lib/api/clients';
import { cn } from '@/lib/utils';

function formatAccountType(type: string): { label: string; icon: LucideIcon } {
  switch (type) {
    case 'CC':
      return { label: 'Conta Corrente', icon: Banknote };
    case 'CR':
      return { label: 'Cartão de Crédito', icon: CreditCard };
    case 'CA':
      return { label: 'Conta Aplicação', icon: PiggyBank };
    case 'CP':
      return { label: 'Conta Poupança', icon: PiggyBank };
    case 'CX':
      return { label: 'Caixinha', icon: Banknote };
    default:
      // Tipos menos comuns (AC, AD, CE, CG, CN, CV, MT, PG) caem aqui.
      // Exibir o código cru evita quebrar a UI se Omie introduzir um valor novo.
      return { label: type, icon: Banknote };
  }
}

export function AccountCard({ account }: { account: BankAccount }) {
  const { label: typeLabel, icon: Icon } = formatAccountType(account.account_type);
  return (
    <div
      className={cn(
        'bg-card flex flex-col gap-2 rounded-lg border p-4 shadow-sm',
        'hover:border-primary/40 transition-colors',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="space-y-0.5">
          <p className="text-sm font-medium leading-tight">{account.name}</p>
          <p className="text-muted-foreground text-xs">{account.bank_name}</p>
        </div>
        <Icon className="text-muted-foreground h-5 w-5 shrink-0" aria-hidden="true" />
      </div>
      <span className="text-muted-foreground text-xs">{typeLabel}</span>
    </div>
  );
}

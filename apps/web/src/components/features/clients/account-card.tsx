/**
 * Card de uma conta bancária Omie — Doc §10.1.
 *
 * `account_type` é tratado como string (memória `feedback_pydantic`):
 * se o Omie introduzir um tipo novo, exibimos o valor cru em vez de quebrar.
 */

import { Banknote, CreditCard, type LucideIcon } from 'lucide-react';

import type { BankAccount } from '@/lib/api/clients';
import { cn } from '@/lib/utils';

function formatAccountType(type: string): { label: string; icon: LucideIcon } {
  if (type === 'CC') return { label: 'Conta Corrente', icon: Banknote };
  if (type === 'CA') return { label: 'Cartão de Crédito', icon: CreditCard };
  return { label: type, icon: Banknote };
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

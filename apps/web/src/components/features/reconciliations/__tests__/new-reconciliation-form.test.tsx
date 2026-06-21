/**
 * Testes do formulário de nova conciliação (FRONT 1.4).
 *
 * Cobre o estado default (conta corrente, nenhuma conta selecionada):
 *   - o campo "Tolerância de Data" foi REMOVIDO (BACK 1.6);
 *   - o label do arquivo é "Arquivo do Extrato" (vira "da Fatura" só p/ cartão);
 *   - não há badge "Cartão de Crédito" sem conta de cartão selecionada.
 *
 * O `ui/select` (Radix) é mockado por stubs: o `@radix-ui/react-select` não
 * resolve no ambiente do vitest (dep `react-remove-scroll`). O sufixo "(Cartão)"
 * no label e o modo-cartão dinâmico são cobertos por `isCreditCardAccount`
 * (unit), pelo `ParsePreview` (props planas) e por E2E.
 */
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/components/ui/select', () => {
  const Pass = ({ children }: { children?: ReactNode }) => <>{children}</>;
  return {
    Select: Pass,
    SelectContent: Pass,
    SelectItem: Pass,
    SelectTrigger: Pass,
    SelectValue: ({ placeholder }: { placeholder?: string }) => <span>{placeholder}</span>,
  };
});

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => ({
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    data: {
      id: 'c1',
      name: 'Cliente X',
      active: true,
      created_at: '2026-04-01T00:00:00Z',
      updated_at: '2026-04-01T00:00:00Z',
      responsible_manager: null,
      reconciliation_count: 0,
      accounts_synced_at: '2026-04-01T00:00:00Z',
      accounts: [
        {
          id: 'a1',
          omie_conta_id: 1,
          name: 'Sicredi 91263-1',
          bank_name: '—',
          account_type: 'CC',
          synced_at: '2026-04-01T00:00:00Z',
        },
        {
          id: 'a2',
          omie_conta_id: 2,
          name: 'Nubank PJ',
          bank_name: '—',
          account_type: 'CR',
          synced_at: '2026-04-01T00:00:00Z',
        },
      ],
    },
  }),
}));

vi.mock('@/hooks/use-reconciliations', () => ({
  useCheckDuplicate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateReconciliation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useParseStatement: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// Import depois dos mocks (vi.mock é hoisted, mas mantém a ordem clara).
import { NewReconciliationForm } from '@/components/features/reconciliations/new-reconciliation-form';

describe('NewReconciliationForm — estado default (conta corrente)', () => {
  it('renderiza o formulário sem o campo de tolerância de data', () => {
    render(<NewReconciliationForm clientId="c1" />);
    expect(screen.getByRole('heading', { name: 'Nova Conciliação' })).toBeVisible();
    expect(screen.queryByText('Tolerância de Data')).toBeNull();
    expect(screen.queryByLabelText('Tolerância de data')).toBeNull();
  });

  it('usa o label "Arquivo do Extrato" e não mostra badge de cartão', () => {
    render(<NewReconciliationForm clientId="c1" />);
    expect(screen.getByText('Arquivo do Extrato')).toBeVisible();
    expect(screen.queryByText('Arquivo da Fatura')).toBeNull();
    expect(screen.queryByText('Cartão de Crédito')).toBeNull();
  });
});

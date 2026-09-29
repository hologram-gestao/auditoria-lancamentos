'use client';

/**
 * Seletor de conta do plano contábil do CLIENTE (Sprint 16 — FRONT 16.5 · 16.6).
 *
 * Um componente só para os dois lugares que escolhem conta: a conta do BANCO
 * de cada conta de origem (tela "Plano contábil") e o alvo da decisão do
 * de-para no destino `conta_contabil`. Os dois pedem a MESMA coisa ao
 * servidor: só contas ANALÍTICAS e ATIVAS (`type=analitica&status=ativa` — o
 * que o backend chama de `postable`). Sintética e inativa nem aparecem; se
 * chegarem por corrida, o 422 `CONTA_CONTABIL_NAO_LANCAVEL` vira erro do campo
 * em quem chama.
 *
 * **Busca no servidor, por PREFIXO de código.** O nome é cifrado e não é
 * buscável (§4.1): o campo diz "Buscar por código". Cada termo, com debounce,
 * refaz a query com `pageSize` no teto (100); passando disso, a lista avisa
 * para refinar em vez de fingir que mostrou tudo.
 */

import { useState } from 'react';

import { Combobox } from '@/components/ui/combobox';
import { useAccountingChartList } from '@/hooks/use-client-accounting-chart';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { ACCOUNTING_CHART_MAX_PAGE_SIZE } from '@/lib/api/client-accounting-chart';
import type { AccountingAccount } from '@/lib/contracts';

/** "649 — Banco conta movimento". Nome indecifrável não entra como se fosse nome. */
export function accountingAccountLabel(account: {
  code: string;
  name: string;
  nameResolved: boolean;
}): string {
  return account.nameResolved ? `${account.code} — ${account.name}` : account.code;
}

interface AccountingAccountComboboxProps {
  clientId: string;
  /** `id` da conta escolhida, ou `null`. */
  value: string | null;
  onValueChange: (accountId: string, account: AccountingAccount | null) => void;
  /** Nome acessível (obrigatório — ex.: "Conta do banco", "Conta contábil"). */
  label: string;
  /** Rótulo do valor atual quando ele não está na página buscada (ex.: a associação vigente). */
  selectedLabel?: string | null;
  disabled?: boolean;
  id?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean;
  className?: string;
}

export function AccountingAccountCombobox({
  clientId,
  value,
  onValueChange,
  label,
  selectedLabel = null,
  disabled = false,
  id,
  'aria-describedby': ariaDescribedBy,
  'aria-invalid': ariaInvalid,
  className,
}: AccountingAccountComboboxProps) {
  const [search, setSearch] = useState('');
  const debounced = useDebouncedValue(search.trim(), 300);
  const query = useAccountingChartList(clientId, {
    page: 1,
    pageSize: ACCOUNTING_CHART_MAX_PAGE_SIZE,
    type: 'analitica',
    status: 'ativa',
    code: debounced || null,
  });

  const accounts = query.data?.data ?? [];
  const total = query.data?.pagination.total ?? 0;
  const options = accounts.map((account) => ({
    value: account.id,
    label: accountingAccountLabel(account),
  }));

  return (
    <Combobox
      id={id}
      aria-describedby={ariaDescribedBy}
      aria-invalid={ariaInvalid}
      className={className}
      options={options}
      value={value}
      selectedLabel={selectedLabel}
      onValueChange={(next) =>
        onValueChange(next, accounts.find((account) => account.id === next) ?? null)
      }
      onSearchChange={setSearch}
      label={label}
      placeholder="Escolher conta analítica ativa"
      searchPlaceholder="Buscar por código"
      emptyMessage={
        query.isError
          ? 'Não foi possível carregar as contas. Feche e abra de novo.'
          : query.isFetching
            ? 'Buscando contas…'
            : debounced
              ? `Nenhuma conta analítica ativa com código começando em "${debounced}".`
              : 'Nenhuma conta analítica ativa no plano contábil.'
      }
      listHint={
        total > accounts.length
          ? `Mostrando ${accounts.length} de ${total} contas — digite o início do código para refinar.`
          : null
      }
      disabled={disabled}
      loading={query.isLoading}
    />
  );
}

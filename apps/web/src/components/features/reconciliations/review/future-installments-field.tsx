'use client';

/**
 * Parcelas FUTURAS de uma compra parcelada na gaveta "Lançar no Omie" (86e3qhhtb).
 *
 * Duas formas, e a tela nunca calcula data:
 *   - a linha ainda não tem série: um interruptor "Provisionar também as M−N
 *     parcelas restantes" (desligado por padrão), e, ligado, a lista que a pessoa
 *     confere — número, vencimento, emissão e valor — vinda do SERVIDOR
 *     (`installment.futuras`, a mesma função que cria os títulos);
 *   - a linha já provisionou a série: a lista TRAVADA, parcela a parcela, com o
 *     título de cada uma ou o motivo de ter parado. Retomar continua das que faltam.
 *
 * O interruptor só existe com a flag ligada (`installmentPostingEnabled`); a série
 * já gravada aparece sempre, porque é o que existe no Omie.
 */

import { Lock } from 'lucide-react';
import { useId } from 'react';

import { Money } from '@/components/shared/money';
import { Switch } from '@/components/ui/switch';
import type { FileEntryInstallment, OmiePostingInstallmentState } from '@/lib/api/omie-postings';
import { formatBRDate } from '@/lib/format';

interface FutureInstallmentsFieldProps {
  installment: FileEntryInstallment | null;
  /** A série que esta linha JÁ provisionou (vazia sem série). */
  series: readonly OmiePostingInstallmentState[];
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  enabled: boolean;
  purchaseLabel: string;
  disabled: boolean;
}

export function FutureInstallmentsField({
  installment,
  series,
  checked,
  onCheckedChange,
  enabled,
  purchaseLabel,
  disabled,
}: FutureInstallmentsFieldProps) {
  const switchId = useId();
  if (series.length > 0) return <LockedSeries series={series} />;
  const futuras = installment?.futuras ?? [];
  if (!enabled || installment === null || futuras.length === 0) return null;

  const restantes = futuras.length;
  const label =
    restantes === 1
      ? 'Provisionar também a parcela restante'
      : `Provisionar também as ${restantes} parcelas restantes`;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Switch
          id={switchId}
          checked={checked}
          disabled={disabled}
          aria-label={`${label} — ${purchaseLabel}`}
          onCheckedChange={onCheckedChange}
        />
        <label htmlFor={switchId} className="cursor-pointer text-sm">
          {label}{' '}
          <span className="text-muted-foreground">
            (parcela {installment.numero} de {installment.total})
          </span>
        </label>
      </div>
      {checked && (
        <div className="bg-muted/40 space-y-2 rounded-md border p-3">
          <ul
            className="space-y-1 text-sm"
            aria-label={`Parcelas a provisionar — ${purchaseLabel}`}
          >
            {futuras.map((p) => (
              <li key={p.numero} className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                <span className="w-24 font-medium tabular-nums">
                  Parcela {p.numero}/{p.total}
                </span>
                <span className="text-muted-foreground tabular-nums">
                  vence {formatBRDate(p.vencimento)}
                </span>
                <Money value={p.valor} className="ml-auto text-sm" />
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground text-xs">
            Cada parcela vira um título a vencer no Contas a Pagar, com o mesmo fornecedor,
            categorias e rateio desta compra, sem baixa: a baixa vem com a fatura em que ela cair, e
            a fatura seguinte encontra o título em vez de criar outro.
          </p>
        </div>
      )}
    </div>
  );
}

function LockedSeries({ series }: { series: readonly OmiePostingInstallmentState[] }) {
  const pendentes = series.filter((p) => p.step !== 'concluido').length;
  return (
    <div className="bg-muted/40 space-y-2 rounded-md border p-3 text-xs">
      <p className="text-muted-foreground flex items-start gap-2">
        <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>
          <strong className="text-foreground font-medium">
            Parcelas futuras já provisionadas por esta compra
          </strong>
          {pendentes > 0
            ? `. ${pendentes === 1 ? '1 parcela ainda não entrou' : `${pendentes} parcelas ainda não entraram`}: enviar de novo continua delas, sem recriar as outras.`
            : '.'}
        </span>
      </p>
      <ul className="space-y-1">
        {series.map((p) => (
          <li key={p.numero} className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
            <span className="text-foreground w-24 font-medium tabular-nums">
              Parcela {p.numero}/{p.total}
            </span>
            <span className="text-muted-foreground tabular-nums">
              vence {formatBRDate(p.vencimento)}
            </span>
            <span className={p.step === 'concluido' ? 'text-muted-foreground' : 'text-warning'}>
              {p.step === 'concluido'
                ? `título nº ${p.payable_id ?? '—'}`
                : (p.error_message ?? 'sem confirmação do Omie')}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

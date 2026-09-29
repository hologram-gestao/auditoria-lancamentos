'use client';

/**
 * Gaveta "Conta do banco" de UMA conta de origem (Sprint 16 — FRONT 16.5 / R3).
 *
 * Define ou TROCA a conta contábil do banco — o lado fixo da partida de toda
 * linha que vem daquela conta. É configuração (upsert), não vigência: trocar
 * não altera materialização já feita (o código fica no snapshot), e a gaveta
 * diz isso.
 *
 * O seletor oferece só contas ANALÍTICAS e ATIVAS (filtro do servidor). O 422
 * `CONTA_CONTABIL_NAO_LANCAVEL` (corrida: a conta foi inativada entre a busca e
 * o salvar) é mensagem NO CAMPO, ligada por `aria-describedby` — nunca toast.
 * Remontada por `key` a cada abertura (estado limpo).
 */

import { Loader2 } from 'lucide-react';
import { useId, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useSetSourceAccountBinding } from '@/hooks/use-client-accounting-chart';
import { readNotPostableMessage } from '@/lib/accounting-chart-errors';
import { ApiError } from '@/lib/api/client';
import type { SourceAccountEntry } from '@/lib/contracts';

import { AccountingAccountCombobox, accountingAccountLabel } from './accounting-account-combobox';
import {
  sourceAccountDetail,
  sourceAccountName,
  type OmieAccountNames,
} from './source-account-label';

interface BankAccountBindingSheetProps {
  clientId: string;
  /** A conta de origem sendo editada; `null` = gaveta fechada. */
  entry: SourceAccountEntry | null;
  omieNames: OmieAccountNames;
  onOpenChange: (open: boolean) => void;
}

export function BankAccountBindingSheet({
  clientId,
  entry,
  omieNames,
  onOpenChange,
}: BankAccountBindingSheetProps) {
  const mutation = useSetSourceAccountBinding(clientId);
  const current = entry?.bankAccount ?? null;
  const [accountId, setAccountId] = useState<string | null>(current?.id ?? null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const fieldId = useId();
  const errorId = `${fieldId}-error`;
  const hintId = `${fieldId}-hint`;

  const isPending = mutation.isPending;
  const unchanged = accountId !== null && accountId === current?.id && current.postable;

  async function save() {
    if (entry === null) return;
    if (accountId === null) {
      setFieldError('Escolha a conta contábil do banco.');
      return;
    }
    setFieldError(null);
    try {
      const result = await mutation.mutateAsync({
        sourceType: entry.sourceType,
        sourceAccountId: entry.sourceAccountId ?? null,
        accountingAccountId: accountId,
      });
      toast.success(result.created ? 'Conta do banco associada.' : 'Conta do banco trocada.');
      onOpenChange(false);
    } catch (err) {
      const notPostable = readNotPostableMessage(err);
      if (notPostable !== null) {
        setFieldError(notPostable);
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível salvar a conta do banco.',
      );
    }
  }

  return (
    <Sheet open={entry !== null} onOpenChange={(next) => !isPending && onOpenChange(next)}>
      <SheetContent side="right" className="flex flex-col p-0">
        <SheetHeader>
          <SheetTitle>Conta do banco</SheetTitle>
          <SheetDescription>
            A conta contábil que fica do outro lado da partida de toda linha desta conta de origem:
            débito ou crédito conforme o sinal do movimento.
          </SheetDescription>
        </SheetHeader>

        <form
          className="flex min-h-0 flex-1 flex-col"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <SheetBody className="space-y-5">
            {entry !== null && (
              <div className="bg-muted/50 space-y-0.5 rounded-lg border p-3 text-sm">
                <p className="text-muted-foreground text-xs">Conta de origem</p>
                <p className="font-medium">{sourceAccountName(entry, omieNames)}</p>
                <p className="text-muted-foreground text-xs">
                  {sourceAccountDetail(entry, omieNames)}
                </p>
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor={fieldId}>Conta contábil do banco</Label>
              <AccountingAccountCombobox
                id={fieldId}
                clientId={clientId}
                value={accountId}
                selectedLabel={current !== null ? accountingAccountLabel(current) : null}
                onValueChange={(next) => {
                  setAccountId(next);
                  setFieldError(null);
                }}
                label="Conta contábil do banco"
                disabled={isPending}
                aria-invalid={fieldError !== null}
                aria-describedby={fieldError !== null ? `${errorId} ${hintId}` : hintId}
              />
              <p id={hintId} className="text-muted-foreground text-xs">
                Só contas analíticas e ativas do plano contábil do cliente. Busca pelo início do
                código.
              </p>
              {fieldError !== null && (
                <p id={errorId} role="alert" className="text-destructive text-sm font-medium">
                  {fieldError}
                </p>
              )}
            </div>

            {current !== null && !current.postable && (
              <p className="bg-warning-muted text-warning ring-warning/30 rounded-lg p-3 text-sm ring-1 ring-inset">
                A conta associada hoje ({accountingAccountLabel(current)}) deixou de ser analítica e
                ativa. A associação continua valendo, mas trocar exige uma conta lançável.
              </p>
            )}

            <p className="text-muted-foreground text-xs">
              Trocar a conta do banco não muda o que já foi materializado: cada versão guarda o
              código que valia quando foi gerada.
            </p>
          </SheetBody>

          <SheetFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isPending}
            >
              Cancelar
            </Button>
            <Button type="submit" disabled={isPending || unchanged}>
              {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              {isPending ? 'Salvando…' : 'Salvar'}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

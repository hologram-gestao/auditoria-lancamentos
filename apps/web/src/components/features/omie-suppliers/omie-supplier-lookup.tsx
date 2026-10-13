'use client';

/**
 * Busca de fornecedor no Omie do cliente pelo CNPJ (86e3n70q0, bloco C).
 *
 * UMA peça para os dois lugares que escolhem fornecedor: a gaveta "Lançar no
 * Omie" (o fornecedor do título de cada compra) e a edição do cliente (o
 * fornecedor PADRÃO). Duas buscas com regras próprias divergiriam no dia em que
 * uma ganhasse uma conferência que a outra não tem.
 *
 * - A busca é AÇÃO ("Buscar no Omie"), nunca por tecla: cada busca é uma ida à
 *   Omie do cliente.
 * - Três desfechos: encontrado (mostra razão social e nome fantasia; o código
 *   fica no pedido), sem cadastro (404 — na gaveta, pede razão social e e-mail
 *   para cadastrar no lançamento; na configuração, diz que o padrão tem de existir
 *   no Omie) e erro (a mensagem do servidor, com "Tentar de novo").
 * - O CNPJ é conferido aqui pela regra do servidor (`lib/cnpj.ts`) antes de sair.
 *
 * O componente é CONTROLADO no que importa ao pedido (`SupplierValue`): quem o usa
 * guarda o valor e pode copiá-lo para outra linha ("Aplicar a todas"). Só o texto
 * digitado e o estado da busca vivem aqui. Nada de CNPJ, nome ou e-mail em log.
 */

import { AlertTriangle, CheckCircle2, Loader2, Search } from 'lucide-react';
import { useEffect, useId, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useFindOmieFornecedor } from '@/hooks/use-omie-postings';
import { ApiError, NetworkError } from '@/lib/api/client';
import { cnpjDigits, formatCnpj, isValidCnpj } from '@/lib/cnpj';
import type { SupplierValue } from '@/lib/validation/omie-posting';

/** Teto do `razao_social` do `IncluirCliente` (`RAZAO_SOCIAL_MAX_LENGTH`). */
const RAZAO_SOCIAL_MAX = 60;
/** Teto do `email` (`EMAIL_MAX_LENGTH`). */
const EMAIL_MAX = 500;

type LookupState =
  | { status: 'idle' }
  | { status: 'invalid' }
  | { status: 'not_found'; message: string }
  | { status: 'error'; message: string };

interface OmieSupplierLookupProps {
  clientId: string;
  value: SupplierValue;
  onChange: (value: SupplierValue) => void;
  /**
   * Gaveta: CNPJ sem cadastro pede razão social e e-mail (o servidor cadastra no
   * lançamento). Configuração do cliente: não — o padrão tem de existir no Omie.
   */
  allowRegister: boolean;
  /** Rótulo VISÍVEL do campo de CNPJ. */
  label: string;
  /**
   * Complemento do nome acessível, só para leitor de tela: numa lista de compras
   * diz de qual linha é o campo, sem repetir na tela o que já está acima dele.
   */
  labelContext?: string;
  disabled?: boolean;
}

export function OmieSupplierLookup({
  clientId,
  value,
  onChange,
  allowRegister,
  label,
  labelContext,
  disabled = false,
}: OmieSupplierLookupProps) {
  const id = useId();
  const findMutation = useFindOmieFornecedor(clientId);
  const [cnpjText, setCnpjText] = useState(() =>
    value.kind === 'found' || value.kind === 'new' ? formatCnpj(value.cnpj) : '',
  );
  const [lookup, setLookup] = useState<LookupState>({ status: 'idle' });

  // O valor pode chegar de fora ("Aplicar a todas"): o campo mostra o CNPJ dele.
  const valueCnpj = value.kind === 'found' || value.kind === 'new' ? value.cnpj : null;
  useEffect(() => {
    if (valueCnpj !== null) {
      setCnpjText(formatCnpj(valueCnpj));
      setLookup({ status: 'idle' });
    }
  }, [valueCnpj]);

  async function search(): Promise<void> {
    if (findMutation.isPending) return;
    if (!isValidCnpj(cnpjText)) {
      setLookup({ status: 'invalid' });
      return;
    }
    const digits = cnpjDigits(cnpjText);
    setLookup({ status: 'idle' });
    try {
      const supplier = await findMutation.mutateAsync({ cnpj: digits });
      onChange({ kind: 'found', cnpj: digits, supplier });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        if (allowRegister) {
          onChange({ kind: 'new', cnpj: digits, razaoSocial: '', email: '' });
        } else {
          setLookup({ status: 'not_found', message: err.userMessage });
          onChange({ kind: 'none' });
        }
        return;
      }
      const message =
        err instanceof ApiError || err instanceof NetworkError
          ? err.userMessage
          : 'Não foi possível buscar o fornecedor no Omie. Tente de novo.';
      setLookup({ status: 'error', message });
    }
  }

  const cnpjId = `${id}-cnpj`;
  const hintId = `${id}-hint`;
  const searching = findMutation.isPending;

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <Label htmlFor={cnpjId}>
          {label}
          {labelContext ? <span className="sr-only"> — {labelContext}</span> : null}
        </Label>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            id={cnpjId}
            inputMode="numeric"
            autoComplete="off"
            placeholder="00.000.000/0000-00"
            value={cnpjText}
            disabled={disabled || searching}
            aria-invalid={lookup.status === 'invalid' || undefined}
            aria-describedby={lookup.status === 'idle' ? undefined : hintId}
            className="min-w-[12.5rem] flex-1 tabular-nums sm:w-56 sm:flex-none"
            onChange={(e) => {
              setCnpjText(formatCnpj(e.target.value));
              setLookup({ status: 'idle' });
              // CNPJ mudou: o que foi achado (ou o cadastro novo) era de OUTRO CNPJ.
              if (value.kind === 'found' || value.kind === 'new') onChange({ kind: 'none' });
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                void search();
              }
            }}
          />
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => void search()}
            disabled={disabled || searching || cnpjDigits(cnpjText).length === 0}
          >
            {searching ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Search className="h-4 w-4" aria-hidden="true" />
            )}
            Buscar no Omie
          </Button>
        </div>
      </div>

      {lookup.status === 'invalid' && (
        <p id={hintId} role="alert" className="text-destructive text-xs">
          CNPJ inválido. Confira os 14 dígitos.
        </p>
      )}
      {lookup.status === 'not_found' && (
        <p id={hintId} role="alert" className="text-warning flex items-start gap-1 text-xs">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          <span>
            Nenhum fornecedor com este CNPJ no Omie do cliente. Cadastre o fornecedor no Omie e
            busque de novo.
          </span>
        </p>
      )}
      {lookup.status === 'error' && (
        <div id={hintId} role="alert" className="text-destructive space-y-1 text-xs">
          <p className="flex items-start gap-1">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            <span>{lookup.message}</span>
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void search()}
            disabled={disabled || searching}
          >
            Tentar de novo
          </Button>
        </div>
      )}

      {value.kind === 'found' && (
        <p role="status" className="text-success flex items-start gap-1 text-sm">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span className="text-foreground">
            <span className="font-medium">{value.supplier.razao_social ?? 'Sem razão social'}</span>
            {value.supplier.nome_fantasia ? (
              <span className="text-muted-foreground"> · {value.supplier.nome_fantasia}</span>
            ) : null}
          </span>
        </p>
      )}

      {value.kind === 'new' && (
        <div className="bg-muted/40 space-y-2 rounded-md border p-3">
          <p role="status" className="text-sm">
            Este CNPJ não tem cadastro no Omie do cliente. Informe razão social e e-mail: o
            fornecedor é cadastrado na hora do lançamento.
          </p>
          <p className="text-muted-foreground text-xs">
            O cadastro não se desfaz pela API. Confira os dados antes de lançar.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor={`${id}-razao`}>Razão social</Label>
              <Input
                id={`${id}-razao`}
                autoComplete="off"
                maxLength={RAZAO_SOCIAL_MAX}
                value={value.razaoSocial}
                disabled={disabled}
                onChange={(e) => onChange({ ...value, razaoSocial: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`${id}-email`}>E-mail</Label>
              <Input
                id={`${id}-email`}
                type="email"
                autoComplete="off"
                maxLength={EMAIL_MAX}
                value={value.email}
                disabled={disabled}
                onChange={(e) => onChange({ ...value, email: e.target.value })}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

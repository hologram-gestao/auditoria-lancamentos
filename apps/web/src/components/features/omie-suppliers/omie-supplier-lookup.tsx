'use client';

/**
 * Busca de fornecedor no Omie do cliente, pelo NOME ou pelo CNPJ (86e3n70q0 bloco
 * C; nome e sugestão desde a 86e3qhhrd).
 *
 * UMA peça para os dois lugares que escolhem fornecedor: a gaveta "Lançar no
 * Omie" (o fornecedor do título de cada compra) e a edição do cliente (o
 * fornecedor PADRÃO). Duas buscas com regras próprias divergiriam no dia em que
 * uma ganhasse uma conferência que a outra não tem.
 *
 * Um campo só, e o que a pessoa digita decide a busca:
 *
 * - **com letra, é nome**: a lista de parecidos aparece quando ela para de digitar
 *   (debounce, mínimo de 3 caracteres úteis). Cada busca vai à NOSSA API, que
 *   procura num diretório em memória do Omie do cliente: nenhuma ida à Omie por
 *   tecla. "Atualizar lista" relê o diretório (fornecedor criado agora no Omie);
 * - **só dígitos, é CNPJ**: a máscara entra sozinha e a busca é AÇÃO ("Buscar no
 *   Omie" ou Enter), como sempre. CNPJ sem cadastro: na gaveta, pede razão social
 *   e e-mail para cadastrar no lançamento; na configuração, diz que o padrão tem
 *   de existir no Omie.
 *
 * Com `suggestions` (gaveta), os cadastros que o servidor achou pela descrição da
 * compra aparecem antes de a pessoa digitar; ela CONFIRMA com um clique, nada é
 * aplicado sozinho. Escolhido o cadastro, o campo some e fica o resumo com "Trocar
 * fornecedor": o pedido leva o CÓDIGO desse cadastro.
 *
 * O componente é CONTROLADO no que importa ao pedido (`SupplierValue`). Só o texto
 * digitado e o estado da busca vivem aqui. Nada de CNPJ, nome ou e-mail em log.
 */

import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Search } from 'lucide-react';
import { useEffect, useId, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import {
  SUPPLIER_SEARCH_MIN_CHARS,
  supplierSearchUsefulLength,
  useFindOmieFornecedor,
  useRefreshOmieFornecedores,
  useSearchOmieFornecedores,
} from '@/hooks/use-omie-postings';
import { ApiError, NetworkError } from '@/lib/api/client';
import { cnpjDigits, formatCnpj, isValidCnpj } from '@/lib/cnpj';
import type { OmieFornecedorItem } from '@/lib/contracts';
import type { SupplierValue } from '@/lib/validation/omie-posting';

/** Teto do `razao_social` do `IncluirCliente` (`RAZAO_SOCIAL_MAX_LENGTH`). */
const RAZAO_SOCIAL_MAX = 60;
/** Teto do `email` (`EMAIL_MAX_LENGTH`). */
const EMAIL_MAX = 500;
/** Teto do texto da busca pelo nome (o mesmo do servidor). */
const SEARCH_MAX = 60;
/** Pausa na digitação antes de buscar pelo nome. */
const SEARCH_DEBOUNCE_MS = 350;

type CnpjLookupState =
  | { status: 'idle' }
  | { status: 'invalid' }
  | { status: 'not_found'; message: string }
  | { status: 'error'; message: string };

/** Com letra é nome; só dígitos e pontuação de máscara é CNPJ. */
function isNameSearch(text: string): boolean {
  return /\p{L}/u.test(text);
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError || err instanceof NetworkError ? err.userMessage : fallback;
}

/** Como o cadastro aparece na tela: razão social, ou nome fantasia, ou o código. */
function supplierTitle(item: OmieFornecedorItem): string {
  return item.razao_social ?? item.nome_fantasia ?? `Cadastro ${item.codigo}`;
}

interface OmieSupplierLookupProps {
  clientId: string;
  value: SupplierValue;
  onChange: (value: SupplierValue) => void;
  /**
   * Gaveta: CNPJ sem cadastro pede razão social e e-mail (o servidor cadastra no
   * lançamento). Configuração do cliente: não — o padrão tem de existir no Omie.
   */
  allowRegister: boolean;
  /** Rótulo VISÍVEL do campo. */
  label: string;
  /**
   * Complemento do nome acessível, só para leitor de tela: numa lista de compras
   * diz de qual linha é o campo, sem repetir na tela o que já está acima dele.
   */
  labelContext?: string;
  /** Cadastros sugeridos pela descrição da compra (gaveta); vazio = sem sugestão. */
  suggestions?: readonly OmieFornecedorItem[];
  disabled?: boolean;
}

export function OmieSupplierLookup({
  clientId,
  value,
  onChange,
  allowRegister,
  label,
  labelContext,
  suggestions = [],
  disabled = false,
}: OmieSupplierLookupProps) {
  const id = useId();
  const findMutation = useFindOmieFornecedor(clientId);
  const refreshMutation = useRefreshOmieFornecedores(clientId);
  const [text, setText] = useState(() => (value.kind === 'new' ? formatCnpj(value.cnpj) : ''));
  const [cnpjLookup, setCnpjLookup] = useState<CnpjLookupState>({ status: 'idle' });

  // O valor pode chegar de fora ("Aplicar a todas"): o campo mostra o CNPJ dele.
  const newCnpj = value.kind === 'new' ? value.cnpj : null;
  useEffect(() => {
    if (newCnpj !== null) {
      setText(formatCnpj(newCnpj));
      setCnpjLookup({ status: 'idle' });
    }
  }, [newCnpj]);

  const nameMode = isNameSearch(text);
  const debouncedText = useDebouncedValue(nameMode ? text : '', SEARCH_DEBOUNCE_MS);
  const searchQuery = useSearchOmieFornecedores(
    clientId,
    value.kind === 'found' ? '' : debouncedText,
  );
  const usefulLength = supplierSearchUsefulLength(text);
  const searching = findMutation.isPending;

  function choose(supplier: OmieFornecedorItem): void {
    setCnpjLookup({ status: 'idle' });
    onChange({ kind: 'found', supplier });
  }

  async function searchCnpj(): Promise<void> {
    if (findMutation.isPending) return;
    if (!isValidCnpj(text)) {
      setCnpjLookup({ status: 'invalid' });
      return;
    }
    const digits = cnpjDigits(text);
    setCnpjLookup({ status: 'idle' });
    try {
      choose(await findMutation.mutateAsync({ cnpj: digits }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        if (allowRegister) {
          onChange({ kind: 'new', cnpj: digits, razaoSocial: '', email: '' });
        } else {
          setCnpjLookup({ status: 'not_found', message: err.userMessage });
          onChange({ kind: 'none' });
        }
        return;
      }
      setCnpjLookup({
        status: 'error',
        message: errorMessage(err, 'Não foi possível buscar o fornecedor no Omie. Tente de novo.'),
      });
    }
  }

  if (value.kind === 'found') {
    return (
      <ChosenSupplier
        supplier={value.supplier}
        disabled={disabled}
        labelContext={labelContext}
        onChangeSupplier={() => {
          setText('');
          onChange({ kind: 'none' });
        }}
      />
    );
  }

  const inputId = `${id}-input`;
  const hintId = `${id}-hint`;
  const cnpjHintId = `${id}-cnpj-hint`;
  const resultsId = `${id}-results`;
  const showSuggestions = suggestions.length > 0 && text.trim() === '' && value.kind === 'none';
  const showNameResults = nameMode && usefulLength >= SUPPLIER_SEARCH_MIN_CHARS;
  const describedBy = [hintId, cnpjLookup.status === 'idle' ? null : cnpjHintId]
    .filter(Boolean)
    .join(' ');

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <Label htmlFor={inputId}>
          {label}
          {labelContext ? <span className="sr-only"> — {labelContext}</span> : null}
        </Label>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            id={inputId}
            autoComplete="off"
            placeholder="Nome ou CNPJ do fornecedor"
            maxLength={SEARCH_MAX}
            value={text}
            disabled={disabled || searching}
            aria-invalid={cnpjLookup.status === 'invalid' || undefined}
            aria-describedby={describedBy}
            aria-controls={showNameResults ? resultsId : undefined}
            className="min-w-[12.5rem] flex-1 sm:w-72 sm:flex-none"
            onChange={(e) => {
              const raw = e.target.value;
              setText(isNameSearch(raw) ? raw : formatCnpj(raw));
              setCnpjLookup({ status: 'idle' });
              // O texto mudou: o cadastro novo era de OUTRO CNPJ.
              if (value.kind === 'new') onChange({ kind: 'none' });
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                if (!nameMode) void searchCnpj();
              }
            }}
          />
          {!nameMode && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void searchCnpj()}
              disabled={disabled || searching || cnpjDigits(text).length === 0}
            >
              {searching ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Search className="h-4 w-4" aria-hidden="true" />
              )}
              Buscar no Omie
            </Button>
          )}
        </div>
        <p id={hintId} className="text-muted-foreground text-xs">
          Digite parte do nome (a lista aparece sozinha) ou o CNPJ.
        </p>
      </div>

      {showSuggestions && (
        <div className="space-y-1">
          <p className="text-muted-foreground text-xs">Sugestão pelo nome da compra:</p>
          <SupplierOptions
            items={suggestions}
            label={`Fornecedores sugeridos${labelContext ? ` — ${labelContext}` : ''}`}
            disabled={disabled}
            onChoose={choose}
          />
        </div>
      )}

      {nameMode && usefulLength > 0 && usefulLength < SUPPLIER_SEARCH_MIN_CHARS && (
        <p className="text-muted-foreground text-xs">
          Digite pelo menos {SUPPLIER_SEARCH_MIN_CHARS} letras.
        </p>
      )}

      {showNameResults && (
        <div id={resultsId} className="space-y-1">
          <NameResults
            query={searchQuery}
            pending={debouncedText !== text}
            labelContext={labelContext}
            disabled={disabled}
            onChoose={choose}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled || refreshMutation.isPending}
              onClick={() => refreshMutation.mutate(text)}
            >
              <RefreshCw
                className={`h-4 w-4 ${refreshMutation.isPending ? 'animate-spin' : ''}`}
                aria-hidden="true"
              />
              Atualizar lista
            </Button>
            {refreshMutation.isError && (
              <p role="alert" className="text-destructive text-xs">
                {errorMessage(
                  refreshMutation.error,
                  'Não foi possível atualizar a lista de fornecedores. Tente de novo.',
                )}
              </p>
            )}
          </div>
        </div>
      )}

      {cnpjLookup.status === 'invalid' && (
        <p id={cnpjHintId} role="alert" className="text-destructive text-xs">
          CNPJ inválido. Confira os 14 dígitos.
        </p>
      )}
      {cnpjLookup.status === 'not_found' && (
        <p id={cnpjHintId} role="alert" className="text-warning flex items-start gap-1 text-xs">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          <span>
            Nenhum fornecedor com este CNPJ no Omie do cliente. Cadastre o fornecedor no Omie e
            busque de novo.
          </span>
        </p>
      )}
      {cnpjLookup.status === 'error' && (
        <div id={cnpjHintId} role="alert" className="text-destructive space-y-1 text-xs">
          <p className="flex items-start gap-1">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            <span>{cnpjLookup.message}</span>
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void searchCnpj()}
            disabled={disabled || searching}
          >
            Tentar de novo
          </Button>
        </div>
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

/** O que a busca pelo nome mostra: buscando, erro, nada, ou a lista. */
function NameResults({
  query,
  pending,
  labelContext,
  disabled,
  onChoose,
}: {
  query: ReturnType<typeof useSearchOmieFornecedores>;
  /** A pessoa ainda está digitando: a lista na tela é a da busca anterior. */
  pending: boolean;
  labelContext?: string;
  disabled: boolean;
  onChoose: (supplier: OmieFornecedorItem) => void;
}) {
  if (query.isError) {
    return (
      <div role="alert" className="text-destructive space-y-1 text-xs">
        <p className="flex items-start gap-1">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          <span>
            {errorMessage(
              query.error,
              'Não foi possível buscar os fornecedores no Omie. Tente de novo.',
            )}
          </span>
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void query.refetch()}
          disabled={disabled || query.isFetching}
        >
          Tentar de novo
        </Button>
      </div>
    );
  }
  const data = query.data;
  if (data === undefined) {
    return (
      <p role="status" className="text-muted-foreground flex items-center gap-1 text-xs">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        Buscando fornecedores no Omie do cliente…
      </p>
    );
  }
  const busy = pending || query.isFetching;
  return (
    <div className="space-y-1" aria-busy={busy || undefined}>
      <p role="status" className="text-muted-foreground text-xs">
        {data.total === 0
          ? 'Nenhum fornecedor ativo com esse nome no Omie do cliente. Busque pelo CNPJ ou atualize a lista se ele foi cadastrado agora.'
          : data.truncated
            ? `Mostrando ${data.data.length} de ${data.total} fornecedores. Digite mais do nome para refinar.`
            : data.total === 1
              ? '1 fornecedor encontrado.'
              : `${data.total} fornecedores encontrados.`}
      </p>
      {data.data.length > 0 && (
        <SupplierOptions
          items={data.data}
          label={`Fornecedores encontrados${labelContext ? ` — ${labelContext}` : ''}`}
          disabled={disabled}
          onChoose={onChoose}
        />
      )}
    </div>
  );
}

/** Lista de cadastros escolhíveis: cada linha é um botão com quem ele é. */
function SupplierOptions({
  items,
  label,
  disabled,
  onChoose,
}: {
  items: readonly OmieFornecedorItem[];
  label: string;
  disabled: boolean;
  onChoose: (supplier: OmieFornecedorItem) => void;
}) {
  return (
    <ul
      aria-label={label}
      className="divide-border max-h-64 divide-y overflow-y-auto rounded-md border"
    >
      {items.map((item) => (
        <li key={item.codigo}>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onChoose(item)}
            className="hover:bg-muted focus-visible:ring-ring flex w-full cursor-pointer flex-col items-start gap-0.5 px-3 py-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="break-words font-medium">{supplierTitle(item)}</span>
            <span className="text-muted-foreground break-words text-xs">
              {[
                item.nome_fantasia && item.nome_fantasia !== item.razao_social
                  ? item.nome_fantasia
                  : null,
                item.cnpj_cpf ? item.cnpj_cpf : 'sem CNPJ',
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** O cadastro escolhido, com o que a pessoa precisa para conferir e trocar. */
function ChosenSupplier({
  supplier,
  disabled,
  labelContext,
  onChangeSupplier,
}: {
  supplier: OmieFornecedorItem;
  disabled: boolean;
  labelContext?: string;
  onChangeSupplier: () => void;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-2">
      <p role="status" className="text-success flex min-w-0 items-start gap-1 text-sm">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="text-foreground min-w-0 break-words">
          <span className="font-medium">{supplierTitle(supplier)}</span>
          {supplier.nome_fantasia && supplier.nome_fantasia !== supplier.razao_social ? (
            <span className="text-muted-foreground"> · {supplier.nome_fantasia}</span>
          ) : null}
          {supplier.cnpj_cpf ? (
            <span className="text-muted-foreground tabular-nums"> · {supplier.cnpj_cpf}</span>
          ) : null}
        </span>
      </p>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={onChangeSupplier}
      >
        Trocar fornecedor
        {labelContext ? <span className="sr-only"> — {labelContext}</span> : null}
      </Button>
    </div>
  );
}

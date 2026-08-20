'use client';

/**
 * Controles do lançamento no Omie na tela de revisão (Sprint 7 / FRONT 07.6).
 *
 * Três peças presentacionais — nenhuma sabe fazer request. Quem envia é a
 * gaveta (FRONT 07.7); aqui só existe a porta de entrada, e é de propósito:
 * a ação **grava na contabilidade do cliente** e nunca dispara direto de um
 * clique na tabela.
 *
 * **Por que `aria-disabled` no bloqueio e `disabled` de verdade no envio.**
 * Botão `disabled` some da ordem de foco: quem navega por teclado nunca lê o
 * motivo, e "ação indisponível com motivo acessível" (critério da task) vira
 * letra morta. Então o bloqueio por elegibilidade usa `aria-disabled` +
 * `aria-describedby` — o leitor anuncia "indisponível" **e** a razão, e o
 * `onClick` é inerte. Já o `pending` usa `disabled` real: é transitório, o
 * foco volta assim que termina, e é ele que garante "duplo-clique não dispara
 * duas requisições".
 *
 * Sem cor hardcoded: tudo sai dos tokens do tema (`Button`, `accent-primary`).
 */

import { Loader2, Upload } from 'lucide-react';
import { useEffect, useRef, useId } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import { POSTING_BLOCK_MESSAGE, type PostingBlockReason } from './omie-posting-eligibility';

interface LancarNoOmieButtonProps {
  /** `null` = elegível. Qualquer outro valor desabilita e vira o motivo lido. */
  block: PostingBlockReason | null;
  /** Um lote desta sessão está em voo — nenhuma porta de entrada abre. */
  pending?: boolean;
  onClick: () => void;
}

/** Ação individual da linha: abre a gaveta de lançamento com uma compra só. */
export function LancarNoOmieButton({ block, pending = false, onClick }: LancarNoOmieButtonProps) {
  const reasonId = useId();
  const blocked = block !== null;
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        aria-disabled={blocked || undefined}
        aria-describedby={blocked ? reasonId : undefined}
        disabled={pending}
        className={cn(blocked && 'cursor-not-allowed opacity-50')}
        onClick={() => {
          // `aria-disabled` não impede o clique — a inércia é aqui.
          if (blocked || pending) return;
          onClick();
        }}
      >
        {pending ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <Upload className="h-4 w-4" aria-hidden="true" />
        )}
        Lançar no Omie
      </Button>
      {blocked && (
        <span id={reasonId} className="sr-only">
          {POSTING_BLOCK_MESSAGE[block]}
        </span>
      )}
    </>
  );
}

interface PostingCheckboxProps {
  checked: boolean;
  /** Marcado parcialmente — só o "selecionar todos" da página usa. */
  indeterminate?: boolean;
  disabled?: boolean;
  /** Nome acessível: a `<tr>` inteira não é anunciada, o controle é. */
  label: string;
  onChange: (checked: boolean) => void;
}

/**
 * Checkbox NATIVO, pela mesma razão do radio do modal "Trocar lançamento"
 * (ADR-004-FE-A11Y-SELECT): teclado, papel e estado vêm do browser, sem ARIA
 * escrita à mão. `indeterminate` só existe via DOM — daí o `ref`.
 */
export function PostingCheckbox({
  checked,
  indeterminate = false,
  disabled = false,
  label,
  onChange,
}: PostingCheckboxProps) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current !== null) ref.current.indeterminate = indeterminate && !checked;
  }, [indeterminate, checked]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      aria-label={label}
      onChange={(e) => onChange(e.target.checked)}
      className="accent-primary size-4 cursor-pointer align-middle disabled:cursor-not-allowed disabled:opacity-50"
    />
  );
}

interface LancarLoteBarProps {
  selectedCount: number;
  pending?: boolean;
  onLaunch: () => void;
  onClear: () => void;
}

/**
 * Barra de lote. Aparece só com alguma compra selecionada — barra vazia
 * ocupando espaço em toda revisão de cartão seria ruído permanente.
 *
 * `role="status"` para que a contagem seja anunciada quando muda: quem opera
 * por teclado marca a caixa e não vê o número mudar sozinho.
 */
export function LancarLoteBar({
  selectedCount,
  pending = false,
  onLaunch,
  onClear,
}: LancarLoteBarProps) {
  const plural = selectedCount === 1 ? 'compra selecionada' : 'compras selecionadas';
  return (
    <div className="bg-muted/40 flex flex-wrap items-center justify-between gap-3 rounded-md border px-4 py-2">
      <span role="status" className="text-sm">
        {selectedCount} {plural}
      </span>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onClear} disabled={pending}>
          Limpar seleção
        </Button>
        <Button size="sm" onClick={onLaunch} disabled={pending}>
          {pending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Upload className="h-4 w-4" aria-hidden="true" />
          )}
          Lançar {selectedCount} {selectedCount === 1 ? 'compra' : 'compras'} no Omie
        </Button>
      </div>
    </div>
  );
}

'use client';

/**
 * Campo de arquivo ÚNICO do sistema (86e3gkd4y): gatilho com cara e
 * comportamento de botão, em vez do `<input type="file">` cru, cujo botão
 * nativo não muda o cursor nem reage ao hover (demo de 29/09).
 *
 * Comportamento:
 *   - O `<input type="file">` real fica visualmente escondido (`sr-only`); um
 *     `<label htmlFor>` estilizado como botão dispara o picker do navegador via
 *     comportamento HTML nativo (sem JS). Acessibilidade: foco, teclado e
 *     leitores de tela vão direto no input. O anel de foco aparece no gatilho
 *     visível (`peer-focus-visible`), senão o foco de teclado cairia num
 *     elemento invisível.
 *   - Não usamos `<Button asChild>` aqui: o Slot do Radix mistura props de
 *     botão (`variant`, `disabled`) com o `<label>` e em alguns navegadores
 *     isso "engole" o clique. Estilizamos o label diretamente com
 *     `buttonVariants` — mesma aparência, comportamento nativo preservado.
 *   - RHF não trabalha bem com `register` direto em `<input type="file">`:
 *     o componente é uncontrolled do ponto de vista do `<input>` e reporta o
 *     `File` ao RHF via `onChange` (`files[0]`). O `ref` encaminhado chega ao
 *     input real, então o `field.ref` do RHF continua focando o campo com erro
 *     no submit, e `name`/`onBlur` passam direto.
 *   - Quando há arquivo selecionado, mostra nome + tamanho formatado e botão
 *     "Remover" que limpa o estado. O nome completo é o próprio texto do
 *     elemento (o `truncate` é só visual), por isso não há `title` nativo,
 *     proibido como tooltip (front-gate §4).
 *   - `value` voltando a `null` por fora (reset do formulário, "Remover") zera
 *     também o input nativo: sem isso, escolher DE NOVO o mesmo arquivo não
 *     dispararia `change`.
 *
 * Um arquivo só. O multi-arquivo da gaveta de conciliação tem padrão próprio,
 * inline, e fica fora deste componente de propósito.
 */

import { Paperclip, Upload, X } from 'lucide-react';
import { forwardRef, useCallback, useEffect, useId, useRef } from 'react';

import { Button, buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface FileInputFieldProps {
  /** Aceito do `<input>`, ex.: `'.pdf,.csv,.xls,.xlsx'`. */
  accept: string;
  /** Arquivo atualmente selecionado (vindo do RHF ou do estado da tela). */
  value: File | null;
  onChange: (file: File | null) => void;
  onBlur?: () => void;
  name?: string;
  disabled?: boolean;
  /** Texto do gatilho. */
  label?: string;
  /** ID injetado pelo `<FormControl>` (ou pelo `<Label htmlFor>` da tela). */
  id?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean;
}

const FOCUS_RING_FROM_INPUT =
  'peer-focus-visible:ring-ring peer-focus-visible:ring-offset-background peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-offset-2';

export const FileInputField = forwardRef<HTMLInputElement, FileInputFieldProps>(
  function FileInputField(
    {
      accept,
      value,
      onChange,
      onBlur,
      name,
      disabled,
      label = 'Escolher arquivo',
      id,
      'aria-describedby': ariaDescribedBy,
      'aria-invalid': ariaInvalid,
    },
    forwardedRef,
  ) {
    const fallbackId = useId();
    const inputId = id ?? fallbackId;
    const inputRef = useRef<HTMLInputElement | null>(null);

    const setRefs = useCallback(
      (node: HTMLInputElement | null) => {
        inputRef.current = node;
        if (typeof forwardedRef === 'function') forwardedRef(node);
        else if (forwardedRef) forwardedRef.current = node;
      },
      [forwardedRef],
    );

    useEffect(() => {
      if (value === null && inputRef.current) inputRef.current.value = '';
    }, [value]);

    function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
      onChange(e.target.files?.[0] ?? null);
    }

    return (
      <div className="space-y-2">
        <input
          ref={setRefs}
          id={inputId}
          name={name}
          type="file"
          accept={accept}
          disabled={disabled}
          onChange={handleChange}
          onBlur={onBlur}
          aria-describedby={ariaDescribedBy}
          aria-invalid={ariaInvalid}
          className="peer sr-only"
        />

        {value === null ? (
          <label
            htmlFor={inputId}
            aria-disabled={disabled || undefined}
            className={cn(
              buttonVariants({ variant: 'outline' }),
              'cursor-pointer',
              FOCUS_RING_FROM_INPUT,
              disabled && 'pointer-events-none opacity-50',
              // Espelha o destaque que shadcn dá em outros campos via FormMessage:
              // a borda do gatilho fica vermelha quando o RHF marca o campo como
              // inválido (obrigatório sem arquivo, extensão/tamanho fora).
              ariaInvalid && 'border-destructive text-destructive',
            )}
          >
            <Upload className="h-4 w-4" aria-hidden="true" />
            {label}
          </label>
        ) : (
          <div
            className={cn(
              'bg-muted/40 ring-offset-background flex items-center gap-3 rounded-md border p-3 text-sm',
              FOCUS_RING_FROM_INPUT,
              ariaInvalid && 'border-destructive',
            )}
          >
            <Paperclip className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{value.name}</p>
              <p className="text-muted-foreground text-xs">{formatFileSize(value.size)}</p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onChange(null)}
              disabled={disabled}
              aria-label="Remover arquivo selecionado"
            >
              <X className="h-4 w-4" aria-hidden="true" />
              Remover
            </Button>
          </div>
        )}
      </div>
    );
  },
);

/**
 * Formata o tamanho conforme a faixa:
 *   - `< 1 KB`  → "X bytes"
 *   - `< 1 MB`  → "X.XX KB"
 *   - `≥ 1 MB`  → "X.XX MB"
 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} bytes`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(2)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

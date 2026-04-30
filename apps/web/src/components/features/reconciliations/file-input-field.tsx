'use client';

/**
 * Campo de upload de arquivo da tela "Nova Conciliação" — Doc §11.1, campo 4.
 *
 * Comportamento:
 *   - O `<input type="file">` real fica visualmente escondido (`sr-only`); um
 *     `<label htmlFor>` estilizado como botão dispara o picker do navegador via
 *     comportamento HTML nativo (sem JS). Acessibilidade: foco, teclado e
 *     leitores de tela vão direto no input.
 *   - Não usamos `<Button asChild>` aqui: o Slot do Radix mistura props de
 *     botão (`variant`, `disabled`) com o `<label>` e em alguns navegadores
 *     isso "engole" o clique. Estilizamos o label diretamente com
 *     `buttonVariants` — mesma aparência, comportamento nativo preservado.
 *   - RHF não trabalha bem com `register` direto em `<input type="file">`:
 *     o componente é uncontrolled do ponto de vista do `<input>` e reporta o
 *     `File` ao RHF via `onChange` (`files[0]`).
 *   - Quando há arquivo selecionado, mostra nome + tamanho formatado e botão
 *     "Remover" que limpa o estado.
 *
 * Drag-and-drop NÃO é escopo do `[FRONT 5.1]`.
 */

import { Paperclip, Upload, X } from 'lucide-react';
import { useId, useRef } from 'react';

import { Button, buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface FileInputFieldProps {
  /** Aceito do `<input>`, ex.: `'.pdf,.csv,.xls,.xlsx'`. */
  accept: string;
  /** Arquivo atualmente selecionado (vindo do RHF). */
  value: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
  /** ID injetado pelo `<FormControl>` para acessibilidade. */
  id?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean;
}

export function FileInputField({
  accept,
  value,
  onChange,
  disabled,
  id,
  'aria-describedby': ariaDescribedBy,
  'aria-invalid': ariaInvalid,
}: FileInputFieldProps) {
  const fallbackId = useId();
  const inputId = id ?? fallbackId;
  const inputRef = useRef<HTMLInputElement>(null);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    onChange(file);
  }

  function handleRemove() {
    onChange(null);
    if (inputRef.current) inputRef.current.value = '';
  }

  return (
    <div className="space-y-2">
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={accept}
        disabled={disabled}
        onChange={handleChange}
        aria-describedby={ariaDescribedBy}
        aria-invalid={ariaInvalid}
        className="sr-only"
      />

      {value === null ? (
        <label
          htmlFor={inputId}
          aria-disabled={disabled || undefined}
          className={cn(
            buttonVariants({ variant: 'outline' }),
            'cursor-pointer',
            disabled && 'pointer-events-none opacity-50',
          )}
        >
          <Upload className="h-4 w-4" aria-hidden="true" />
          Escolher arquivo
        </label>
      ) : (
        <div className="bg-muted/40 flex items-center gap-3 rounded-md border p-3 text-sm">
          <Paperclip className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium" title={value.name}>
              {value.name}
            </p>
            <p className="text-muted-foreground text-xs">{formatFileSize(value.size)}</p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleRemove}
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
}

/**
 * Formata o tamanho conforme a faixa:
 *   - `< 1 KB`  → "X bytes"
 *   - `< 1 MB`  → "X.XX KB"
 *   - `≥ 1 MB`  → "X.XX MB"
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} bytes`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(2)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

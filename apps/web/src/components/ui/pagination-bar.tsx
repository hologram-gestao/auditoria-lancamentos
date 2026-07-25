'use client';

/**
 * Rodapé de paginação padrão das listas (design-system).
 *
 * Contrato visual: `x–y de N` · `Página p de T` · itens por página. Fica FIXO
 * no rodapé da lista (mesmo quando há uma página só) para a pessoa sempre saber
 * o tamanho do conjunto — sumir com a barra quando `totalPages === 1` esconde
 * justamente a informação "são só N".
 *
 * Não conhece a origem dos dados: recebe os números do envelope de paginação e
 * devolve intenções (`onPageChange` / `onPageSizeChange`). Quem chama decide se
 * isso vira estado na URL.
 */
import { ChevronLeft, ChevronRight } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const;

export interface PaginationBarProps {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  /** Desabilita a navegação enquanto a 1ª carga está em voo. */
  disabled?: boolean;
  /** Rótulo do conjunto no `aria-label` (ex.: "conciliações"). */
  itemLabel: string;
}

export function PaginationBar({
  page,
  pageSize,
  total,
  totalPages,
  onPageChange,
  onPageSizeChange,
  disabled = false,
  itemLabel,
}: PaginationBarProps) {
  // `x–y de N`: com a lista vazia o intervalo é 0–0, não 1–0.
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);
  const lastPage = Math.max(totalPages, 1);

  return (
    <nav
      aria-label={`Paginação de ${itemLabel}`}
      className="bg-card flex flex-col gap-3 border-t px-1 py-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <p className="text-muted-foreground text-sm" aria-live="polite">
        {first}–{last} de {total}
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label htmlFor="page-size" className="text-muted-foreground text-xs">
            Itens por página
          </label>
          <Select
            value={String(pageSize)}
            onValueChange={(value) => onPageSizeChange(Number(value))}
            disabled={disabled}
          >
            <SelectTrigger id="page-size" className="h-9 w-[84px]" aria-label="Itens por página">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZE_OPTIONS.map((option) => (
                <SelectItem key={option} value={String(option)}>
                  {option}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onPageChange(Math.max(1, page - 1))}
            disabled={disabled || page <= 1}
            aria-label="Página anterior"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            Anterior
          </Button>
          <span className="text-muted-foreground text-sm tabular-nums">
            Página {page} de {lastPage}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onPageChange(page + 1)}
            disabled={disabled || page >= lastPage}
            aria-label="Próxima página"
          >
            Próxima
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </div>
    </nav>
  );
}

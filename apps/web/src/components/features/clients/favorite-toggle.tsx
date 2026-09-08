'use client';

/**
 * Coração de favorito do cliente (86e34jd5a).
 *
 * Favorito é preferência POR USUÁRIO: marca "os meus clientes do dia a dia" e
 * o backend põe esses no topo da lista de quem pediu. Não é edição do cliente
 * — por isso não passa por `hasPermission('edit_client')`: quem enxerga o
 * cliente pode favoritá-lo, e quem não enxerga nem chega a esta tela.
 *
 * Acessibilidade: é um botão de alternância de verdade (`aria-pressed`), com
 * o nome carregando o cliente e a ação ("Favoritar X" / "Remover X dos
 * favoritos"). A dica segue o padrão do design system — nunca `title` nativo.
 * Cor por token: `text-primary` (marcado) e `text-muted-foreground` (não).
 *
 * `stopPropagation` no clique: a linha da lista navega ao ser clicada, e
 * favoritar não pode virar navegação. Vale para teclado também — Enter/Espaço
 * no botão disparam o mesmo `click` sintético.
 */
import { Heart, Loader2 } from 'lucide-react';
import type { MouseEvent } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useSetFavorite } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import { cn } from '@/lib/utils';

interface FavoriteToggleProps {
  clientId: string;
  clientName: string;
  isFavorite: boolean;
  className?: string;
}

export function FavoriteToggle({
  clientId,
  clientName,
  isFavorite,
  className,
}: FavoriteToggleProps) {
  const mutation = useSetFavorite(clientId);
  const label = isFavorite ? `Remover ${clientName} dos favoritos` : `Favoritar ${clientName}`;

  const onClick = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    mutation.mutate(!isFavorite, {
      onError: (err) => {
        toast.error(
          err instanceof ApiError ? err.userMessage : 'Não foi possível atualizar o favorito.',
        );
      },
    });
  };

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-pressed={isFavorite}
            aria-label={label}
            disabled={mutation.isPending}
            onClick={onClick}
            className={cn(isFavorite ? 'text-primary' : 'text-muted-foreground', className)}
          >
            {mutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Heart className={cn('h-4 w-4', isFavorite && 'fill-current')} aria-hidden="true" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs leading-snug">
          {isFavorite
            ? 'Favorito: fica no topo da sua lista'
            : 'Favoritar: sobe para o topo da sua lista'}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

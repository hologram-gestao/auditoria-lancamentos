'use client';

/**
 * Nome de quem fez a conciliação, com o e-mail na dica ACESSÍVEL (86e2n39f1
 * — decisão do Pedro: "nome na tela, e-mail na dica"). Mesmo padrão de
 * tooltip do resto da revisão (86e2u513n): foco por teclado, toque e leitor
 * de tela, nunca `title` nativo.
 *
 * `email === null` é o autor MASCARADO pelo servidor ("Equipe Hologram" para
 * usuário de tenant vendo autor da equipe) — sem dica, porque não há nada a
 * revelar; vira texto simples.
 */
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

export interface AuthorInfo {
  name: string;
  email?: string | null;
}

export function AuthorLabel({ author }: { author: AuthorInfo }) {
  if (!author.email) {
    return <span>{author.name}</span>;
  }
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="img"
            tabIndex={0}
            aria-label={`${author.name} — ${author.email}`}
            className="focus-visible:ring-ring cursor-default underline decoration-dotted underline-offset-2 focus-visible:outline-none focus-visible:ring-2"
          >
            {author.name}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs leading-snug">
          {author.email}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * Estado vazio com vinheta (86e3h57b5): a moldura ÚNICA do "ainda não há nada aqui".
 *
 * Antes cada tela escrevia o seu `div` com `border-dashed`; agora quem chama passa a
 * vinheta (`components/shared/vignettes.tsx`), o título, a descrição e, opcional, a
 * ação. A ação é um SLOT: quem a passa já decidiu pela permissão (§7 Frontend: aviso
 * que aponta para uma ação só aparece para quem pode fazê-la). O texto é o da tela;
 * este componente não escreve texto nenhum.
 *
 * Duas molduras:
 *   - `framed` (padrão): borda tracejada, para o vazio que ocupa o lugar de um bloco;
 *   - `framed={false}`: sem borda, para dentro de um `TableEmpty` ou de um card que já
 *     tem a própria moldura. Aí a vinheta vem pequena (`size="sm"`).
 *
 * `announce` liga `role="status"`: use quando o vazio SUBSTITUI um conteúdo que
 * carregou (o resultado de uma busca, a lista que veio vazia), para o leitor de tela
 * ouvir a troca. Server component.
 */
import { cn } from '@/lib/utils';

export interface EmptyStateProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'title'> {
  /** O desenho, decorativo (as vinhetas já saem com `aria-hidden`). */
  vignette: React.ReactNode;
  /** Frase de destaque (opcional: há vazio de uma frase só). */
  title?: React.ReactNode;
  /** O texto de apoio, em `muted`. */
  description?: React.ReactNode;
  /** A ação, já decidida pela permissão de quem chama. */
  action?: React.ReactNode;
  /** `role="status"`: o vazio substitui um conteúdo carregado. */
  announce?: boolean;
  /** Borda tracejada própria (padrão) ou nenhuma, dentro de outra moldura. */
  framed?: boolean;
  /** Tamanho da vinheta: `sm` dentro de tabela e de card, `md` no bloco. */
  size?: 'sm' | 'md';
}

const VIGNETTE_SIZE = {
  sm: 'h-12',
  md: 'h-20',
} as const;

export function EmptyState({
  vignette,
  title,
  description,
  action,
  announce = false,
  framed = true,
  size = framed ? 'md' : 'sm',
  className,
  ...props
}: EmptyStateProps) {
  return (
    <div
      role={announce ? 'status' : undefined}
      className={cn(
        'flex flex-col items-center gap-3 text-center',
        framed && 'rounded-lg border border-dashed p-8',
        className,
      )}
      {...props}
    >
      <div
        className={cn(
          'flex shrink-0 justify-center [&>svg]:h-full [&>svg]:w-auto',
          VIGNETTE_SIZE[size],
        )}
      >
        {vignette}
      </div>
      {(title !== undefined || description !== undefined) && (
        <div className="max-w-prose space-y-1">
          {title !== undefined && <p className="text-sm font-medium">{title}</p>}
          {description !== undefined && (
            <p className="text-muted-foreground text-sm">{description}</p>
          )}
        </div>
      )}
      {action}
    </div>
  );
}

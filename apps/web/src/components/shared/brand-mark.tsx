/**
 * Logomark "H" da Hologram (86e2ukrc9) — pintada por TOKEN, um asset só.
 *
 * O PNG oficial (fundo transparente) vira MÁSCARA CSS e o `bg-current` pinta a
 * forma com o `currentColor` do contexto — em `text-primary` a logo sai marinho
 * no claro, índigo no escuro e branca no tema Hologram, sem variante por tema e
 * sem vetorização de terceiros redesenhando a marca. O arquivo em
 * `public/brand/hologram-h.png` é derivado de `Docs/brand/h-svg-black.png`
 * (256px de altura, 5KB — só o canal alpha importa para a máscara).
 *
 * Decorativa (`aria-hidden`): o nome do produto segue em texto ao lado.
 */
import { cn } from '@/lib/utils';

const MASK: React.CSSProperties = {
  aspectRatio: '277 / 256',
  maskImage: 'url(/brand/hologram-h.png)',
  WebkitMaskImage: 'url(/brand/hologram-h.png)',
  maskSize: 'contain',
  WebkitMaskSize: 'contain',
  maskRepeat: 'no-repeat',
  WebkitMaskRepeat: 'no-repeat',
  maskPosition: 'center',
  WebkitMaskPosition: 'center',
};

export function BrandMark({ className }: { className?: string }) {
  return (
    <span aria-hidden="true" className={cn('inline-block bg-current', className)} style={MASK} />
  );
}

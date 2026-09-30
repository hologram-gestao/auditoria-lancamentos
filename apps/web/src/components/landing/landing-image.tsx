/**
 * Slots de imagem da landing (decisão D3 do plano): 16:9 (`wide`) e 4:3
 * (`standard`). Na v1 NÃO há imagem: a riqueza visual é CSS e a vinheta de produto,
 * e o slot sem arquivo não renderiza nada (nem espaço vazio).
 *
 * Para ligar um slot:
 *   1. baixe a imagem do Magnific e CONFIRA a licença de uso comercial;
 *   2. salve em `apps/web/public/landing/` (a CSP tem `img-src 'self'`: imagem de
 *      fora do domínio não carrega);
 *   3. preencha `LANDING_IMAGES` abaixo com o caminho e um `alt` que descreva a
 *      cena (ou `alt: ''` se for só decorativa).
 */
import Image from 'next/image';

import { cn } from '@/lib/utils';

type ImageSlot = { src: string; alt: string } | null;

export const LANDING_IMAGES: Record<'wide' | 'standard', ImageSlot> = {
  wide: null,
  standard: null,
};

const ASPECT = { wide: 'aspect-video', standard: 'aspect-[4/3]' } as const;

export function LandingImage({
  slot,
  className,
}: {
  slot: keyof typeof LANDING_IMAGES;
  className?: string;
}) {
  const image = LANDING_IMAGES[slot];
  if (!image) return null;
  return (
    <div
      data-reveal
      className={cn('relative overflow-hidden rounded-xl border', ASPECT[slot], className)}
    >
      <Image
        src={image.src}
        alt={image.alt}
        fill
        sizes="(min-width: 1024px) 1152px, 100vw"
        className="object-cover"
      />
    </div>
  );
}

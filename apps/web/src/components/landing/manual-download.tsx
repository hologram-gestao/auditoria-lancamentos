/**
 * Link de download do manual (86e3gr6k5): botão secundário no fim da seção de segurança
 * e link de texto na confirmação do formulário. Mesmo destino, mesmo `download`, e o
 * tamanho ao lado, ligado ao link por `aria-describedby` (quem usa leitor de tela ouve
 * "PDF, 3 MB" antes de baixar 3 MB). Sem `target`: baixa na mesma aba.
 */
import { Download } from 'lucide-react';

import { Button } from '@/components/ui/button';

import { manual } from './content';

export function ManualDownload({
  id,
  variant = 'button',
}: {
  /** Único na página: nomeia o texto do tamanho. */
  id: string;
  variant?: 'button' | 'link';
}) {
  const sizeId = `${id}-tamanho`;
  const size = (
    <span id={sizeId} className="text-muted-foreground text-sm">
      {manual.size}
    </span>
  );
  if (variant === 'link') {
    return (
      <p className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1">
        <a
          href={manual.href}
          download
          aria-describedby={sizeId}
          className="focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-sm font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2"
        >
          <Download aria-hidden="true" className="h-4 w-4" />
          {manual.successLead}
        </a>
        {size}
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button asChild variant="outline">
        <a href={manual.href} download aria-describedby={sizeId}>
          <Download aria-hidden="true" className="h-4 w-4" />
          {manual.label}
        </a>
      </Button>
      {size}
    </div>
  );
}

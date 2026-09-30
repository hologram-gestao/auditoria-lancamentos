/**
 * Moldura de bloco da landing (86e3fr9vz): `section` nomeada pelo próprio `h2`,
 * largura contida e revelação na rolagem (`data-reveal`, ver `reveal.tsx`).
 *
 * Refino de 30/09 (86e3gr6k5): rótulo pequeno em caixa alta acima do `h2`
 * (`eyebrow`), divisor de luz entre uma seção e a seguinte (`.lp-section` no CSS) e,
 * opcional, a grade sutil atrás do bloco (`backdrop="grid"`), decorativa.
 *
 * Vinheta (86e3gwzj0): `aside` é um desenho decorativo ao lado do título, dentro do
 * mesmo `data-reveal` (ele se anima quando o título entra). Some abaixo de `md`.
 */
import { cn } from '@/lib/utils';

export function LandingEyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
      {children}
    </p>
  );
}

export function LandingSection({
  id,
  eyebrow,
  title,
  backdrop,
  aside,
  children,
  className,
}: {
  id: string;
  eyebrow?: string;
  title: string;
  backdrop?: 'grid';
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  const headingId = `${id}-title`;
  return (
    <section
      id={id}
      aria-labelledby={headingId}
      className={cn('lp-section relative isolate py-16 sm:py-24', className)}
    >
      {backdrop === 'grid' && <div aria-hidden="true" className="lp-grid lp-grid--section -z-10" />}
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div
          data-reveal
          className={cn(aside ? 'md:flex md:items-center md:justify-between md:gap-10' : undefined)}
        >
          <div className="min-w-0 max-w-2xl">
            {eyebrow && <LandingEyebrow>{eyebrow}</LandingEyebrow>}
            <h2
              id={headingId}
              className={cn(
                'text-3xl font-semibold leading-tight tracking-tight sm:text-4xl',
                eyebrow && 'mt-3',
              )}
            >
              {title}
            </h2>
          </div>
          {aside && <div className="hidden shrink-0 md:block">{aside}</div>}
        </div>
        <div className="mt-10 sm:mt-12">{children}</div>
      </div>
    </section>
  );
}

/** Atraso escalonado da revelação de itens de uma lista (80 ms por item). */
export function revealDelay(index: number): React.CSSProperties {
  return { '--lp-delay': `${index * 80}ms` } as React.CSSProperties;
}

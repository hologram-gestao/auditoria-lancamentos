/**
 * Moldura de bloco da landing (86e3fr9vz): `section` nomeada pelo próprio `h2`,
 * largura contida e revelação na rolagem (`data-reveal`, ver `reveal.tsx`).
 *
 * Refino de 30/09 (86e3gr6k5): rótulo pequeno em caixa alta acima do `h2`
 * (`eyebrow`), divisor de luz entre uma seção e a seguinte (`.lp-section` no CSS) e,
 * opcional, a grade sutil atrás do bloco (`backdrop="grid"`), decorativa.
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
  children,
  className,
}: {
  id: string;
  eyebrow?: string;
  title: string;
  backdrop?: 'grid';
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
        <div data-reveal className="max-w-2xl">
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
        <div className="mt-10 sm:mt-12">{children}</div>
      </div>
    </section>
  );
}

/** Atraso escalonado da revelação de itens de uma lista (80 ms por item). */
export function revealDelay(index: number): React.CSSProperties {
  return { '--lp-delay': `${index * 80}ms` } as React.CSSProperties;
}

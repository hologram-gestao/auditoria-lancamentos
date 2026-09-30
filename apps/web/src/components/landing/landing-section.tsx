/**
 * Moldura de bloco da landing (86e3fr9vz): `section` nomeada pelo próprio `h2`,
 * largura contida e revelação na rolagem (`data-reveal`, ver `reveal.tsx`).
 */
import { cn } from '@/lib/utils';

export function LandingSection({
  id,
  title,
  children,
  className,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  const headingId = `${id}-title`;
  return (
    <section id={id} aria-labelledby={headingId} className={cn('py-16 sm:py-24', className)}>
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <h2
          id={headingId}
          data-reveal
          className="max-w-2xl text-3xl font-semibold leading-tight tracking-tight sm:text-4xl"
        >
          {title}
        </h2>
        <div className="mt-10 sm:mt-12">{children}</div>
      </div>
    </section>
  );
}

/** Atraso escalonado da revelação de itens de uma lista (80 ms por item). */
export function revealDelay(index: number): React.CSSProperties {
  return { '--lp-delay': `${index * 80}ms` } as React.CSSProperties;
}

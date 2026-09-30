/**
 * Rodapé da landing e do aviso de privacidade (86e3fr9vz). O filete de luz no topo
 * (`.lp-footer::before`, 86e3gr6k5) é decorativo.
 */
import Link from 'next/link';

import { BrandMark } from '@/components/shared/brand-mark';

import { footer } from './content';

export function LandingFooter() {
  // Server component estático: vale o ano do build, e cada deploy o atualiza.
  const year = new Date().getFullYear();
  return (
    <footer className="lp-footer relative border-t">
      <div className="text-muted-foreground mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex items-center gap-2">
          <BrandMark className="text-primary h-5 shrink-0" />
          <span>
            © {year} {footer.company}
          </span>
          <span aria-hidden="true">·</span>
          <span className="text-foreground font-medium">{footer.product}</span>
        </div>
        <nav aria-label={footer.navLabel} className="flex items-center gap-6">
          <Link
            href="/login"
            className="hover:text-foreground focus-visible:ring-ring rounded-sm underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2"
          >
            {footer.signIn}
          </Link>
          <Link
            href="/privacidade"
            className="hover:text-foreground focus-visible:ring-ring rounded-sm underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2"
          >
            {footer.privacy}
          </Link>
        </nav>
      </div>
    </footer>
  );
}

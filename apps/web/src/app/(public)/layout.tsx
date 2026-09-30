/**
 * Layout das páginas PÚBLICAS de marca: a landing (`/`) e o aviso de privacidade
 * (`/privacidade`) — épico 86e3fr9tj.
 *
 * Duas exceções deliberadas às regras do shell autenticado, e o porquê:
 *   - **Tema Hologram fixo** (decisão D1): o wrapper leva a classe `.hologram`, que
 *     redefine os tokens por classe (`globals.css`), então vence o tema salvo no
 *     `<html>`, seja qual for. Não há seletor de tema aqui.
 *   - **Largura contida** (`max-w-6xl` nos blocos): a regra de "largura total" do
 *     `.claude/design-system.md` é do app autenticado, onde a tela é tabela. Uma
 *     página de leitura com linha de 1440px fica ilegível.
 *
 * Quem rola é a janela (altura natural), não um `<main>` com `overflow`, como no app.
 */
import { LandingFooter } from '@/components/landing/landing-footer';
import { LandingHeader } from '@/components/landing/landing-header';
import { LandingEffects } from '@/components/landing/reveal';

import '../public-brand.css';
import './landing.css';

export default function PublicLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="hologram lp-public landing bg-background text-foreground flex min-h-screen flex-col overflow-x-clip font-sans antialiased">
      <LandingHeader />
      <main id="conteudo" className="flex-1">
        {children}
      </main>
      <LandingFooter />
      <LandingEffects />
    </div>
  );
}

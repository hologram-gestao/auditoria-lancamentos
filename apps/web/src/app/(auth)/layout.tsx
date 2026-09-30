/**
 * Layout da tela de login: a ponte entre a landing e o sistema (86e3h1h75).
 *
 * As mesmas duas exceções do layout público (`(public)/layout.tsx`), e pelo mesmo
 * motivo, a "cara pública" é uma só:
 *   - **Tema Hologram fixo**: o wrapper leva `.hologram`, que redefine os tokens por
 *     classe e vence o tema salvo no `<html>` (que continua lá, intocado: depois de
 *     entrar, o tema escolhido volta a valer). Não há seletor de tema aqui.
 *   - **Altura da janela**: quem rola é a janela, não um `<main>` com `overflow`.
 *
 * Duas colunas de `lg` para cima: o painel de marca à esquerda (aurora da landing em
 * intensidade baixa, logomark, a frase do hero) e o formulário à direita. Abaixo de
 * `lg`, só a coluna do formulário, centralizada. O link de volta para o site fica
 * acima do card, alinhado à esquerda dele; o nome da empresa, abaixo.
 */
import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';

import { login } from '@/components/landing/content';
import { SignInBrandPanel } from '@/components/landing/sign-in-brand-panel';

import '../public-brand.css';

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="hologram lp-public bg-background text-foreground grid min-h-screen overflow-x-clip font-sans antialiased lg:grid-cols-2">
      <SignInBrandPanel />
      <main
        id="conteudo"
        className="flex min-w-0 flex-col items-center justify-center px-4 py-12 sm:px-6"
      >
        <div className="flex w-full max-w-sm flex-col gap-6">
          <Link
            href="/"
            className="text-muted-foreground hover:text-foreground focus-visible:ring-ring ring-offset-background inline-flex items-center gap-2 self-start rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            {login.backToSite}
          </Link>
          {children}
          <p className="text-muted-foreground text-center text-xs">{login.footer}</p>
        </div>
      </main>
    </div>
  );
}

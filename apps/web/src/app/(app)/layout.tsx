'use client';

/**
 * Shell mínimo das rotas autenticadas. Header + sidebar; S6 expandirá.
 *
 * Bootstrap da sessão:
 *   - Após F5 o store Zustand zera (sem persistência), mas os cookies HttpOnly
 *     ainda estão lá. O `useEffect` chama `/refresh` para recuperar o user.
 *   - Se o refresh falhar (refresh token expirado/ausente), redireciona p/ login.
 *
 * Dispensar o cookie é trabalho do backend (logout limpa). O Zustand só reflete.
 */

import { AlertTriangle, LogOut, Settings, Users as UsersIcon } from 'lucide-react';
import Link from 'next/link';
import { useRouter, usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { logout as logoutRequest, refreshSession } from '@/lib/api/auth';
import { ApiError } from '@/lib/api/client';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';

interface SidebarLinkProps {
  href: string;
  pathname: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}

function SidebarLink({ href, pathname, icon, children }: SidebarLinkProps) {
  const active = pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      className={cn(
        'flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
        // focus-visible:ring* alinha com o restante da UI (shadcn padrão).
        // Sem isso, o foco do Tab no sidebar caía no outline default do
        // navegador (1px preto), inconsistente com o resto da página.
        'focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
        active ? 'bg-muted text-foreground font-medium' : 'text-muted-foreground hover:bg-muted',
      )}
    >
      {icon}
      <span>{children}</span>
    </Link>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const clearUser = useAuthStore((s) => s.clearUser);
  const [bootstrapped, setBootstrapped] = useState(user !== null);
  // Falha TRANSITÓRIA no bootstrap (5xx / rede) NÃO desloga — mostra um erro
  // recuperável com "tentar novamente". `attempt` força o efeito a re-rodar.
  const [bootstrapError, setBootstrapError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (user !== null) {
      setBootstrapped(true);
      return;
    }
    let cancelled = false;

    async function bootstrap() {
      const MAX_ATTEMPTS = 3;
      for (let n = 1; n <= MAX_ATTEMPTS; n++) {
        try {
          const u = await refreshSession();
          if (cancelled) return;
          setUser(u);
          setBootstrapped(true);
          return;
        } catch (err) {
          if (cancelled) return;
          // 401 = refresh token não vale mais → sessão acabou de verdade.
          if (err instanceof ApiError && err.status === 401) {
            router.replace('/login');
            return;
          }
          // Transitório (5xx / NetworkError): espera (backoff) e tenta de novo.
          if (n < MAX_ATTEMPTS) {
            await new Promise((resolve) => setTimeout(resolve, n * 600));
            continue;
          }
          // Esgotou os retries — NÃO força /login (a sessão provavelmente está
          // viva); oferece reconectar. Era aqui que um soluço do servidor logo
          // após o deploy virava "logout" indevido.
          setBootstrapError(true);
        }
      }
    }

    setBootstrapError(false);
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [user, setUser, router, attempt]);

  async function handleLogout() {
    try {
      await logoutRequest();
    } catch {
      // ignora — logout é best-effort do lado do servidor; sempre limpamos local.
    }
    clearUser();
    router.replace('/login');
  }

  if (bootstrapError && user === null) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 text-sm">
        <p className="text-muted-foreground">
          Não foi possível conectar ao servidor. Sua sessão continua ativa.
        </p>
        <Button variant="outline" size="sm" onClick={() => setAttempt((n) => n + 1)}>
          Tentar novamente
        </Button>
      </div>
    );
  }

  if (!bootstrapped || user === null) {
    return (
      <div className="text-muted-foreground flex min-h-screen items-center justify-center text-sm">
        Carregando...
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="bg-card flex items-center justify-between border-b px-6 py-3">
        <div className="font-semibold">Auditoria de Lançamentos</div>
        <div className="flex items-center gap-4">
          <span className="text-muted-foreground text-sm">
            {user.email}
            <span className="px-2 opacity-60">·</span>
            <span className="capitalize">{user.role}</span>
          </span>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            <LogOut className="h-4 w-4" aria-hidden="true" />
            Sair
          </Button>
        </div>
      </header>
      <div className="flex flex-1">
        <aside className="bg-card/50 hidden w-56 border-r p-4 md:block">
          <nav className="flex flex-col gap-1">
            <SidebarLink
              href="/clientes"
              pathname={pathname}
              icon={<UsersIcon className="h-4 w-4" />}
            >
              Clientes
            </SidebarLink>
            {user.role === 'admin' && (
              <>
                <div className="text-muted-foreground mt-4 px-3 pb-1 text-xs font-medium uppercase tracking-wide">
                  Configurações
                </div>
                <SidebarLink
                  href="/configuracoes/usuarios"
                  pathname={pathname}
                  icon={<Settings className="h-4 w-4" />}
                >
                  Usuários
                </SidebarLink>
                <SidebarLink
                  href="/configuracoes/anomalias"
                  pathname={pathname}
                  icon={<AlertTriangle className="h-4 w-4" />}
                >
                  Tipos de Anomalia
                </SidebarLink>
              </>
            )}
          </nav>
        </aside>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}

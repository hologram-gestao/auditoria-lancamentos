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

import { LogOut } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { logout as logoutRequest, refreshSession } from '@/lib/api/auth';
import { ApiError, NetworkError } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const clearUser = useAuthStore((s) => s.clearUser);
  const [bootstrapped, setBootstrapped] = useState(user !== null);

  useEffect(() => {
    if (user !== null) {
      setBootstrapped(true);
      return;
    }
    let cancelled = false;
    refreshSession()
      .then((u) => {
        if (cancelled) return;
        setUser(u);
        setBootstrapped(true);
      })
      .catch((err) => {
        if (cancelled) return;
        // Refresh falhou — sessão inválida ou erro de rede; volta ao login.
        if (err instanceof ApiError || err instanceof NetworkError) {
          router.replace('/login');
          return;
        }
        router.replace('/login');
      });
    return () => {
      cancelled = true;
    };
  }, [user, setUser, router]);

  async function handleLogout() {
    try {
      await logoutRequest();
    } catch {
      // ignora — logout é best-effort do lado do servidor; sempre limpamos local.
    }
    clearUser();
    router.replace('/login');
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
            <Link href="/clientes" className="hover:bg-muted rounded-md px-3 py-2 text-sm">
              Clientes
            </Link>
          </nav>
        </aside>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}

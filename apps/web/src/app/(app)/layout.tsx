'use client';

/**
 * Shell das rotas autenticadas: header + sidebar em CAMADAS (86e2n39h7 — a
 * árvore e a troca de camada moram em `features/navigation/`).
 *
 * Bootstrap da sessão:
 *   - Após F5 o store Zustand zera (sem persistência), mas os cookies HttpOnly
 *     ainda estão lá. O `useEffect` chama `/refresh` para recuperar o user.
 *   - Se o refresh falhar (refresh token expirado/ausente), redireciona p/ login.
 *
 * Dispensar o cookie é trabalho do backend (logout limpa). O Zustand só reflete.
 */

import { LogOut } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { SidebarNav } from '@/components/features/navigation/sidebar-nav';
import { NotificationBell } from '@/components/features/notifications/notification-bell';
import { NavigationOutcomeTracker } from '@/components/features/reconciliations/create/navigation-outcome-tracker';
import { Button } from '@/components/ui/button';
import { logout as logoutRequest, refreshSession } from '@/lib/api/auth';
import { ApiError } from '@/lib/api/client';
import { roleLabel } from '@/lib/authz';
import { useAuthStore } from '@/stores/auth';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
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
    // Shell FIXO: a viewport inteira (`h-dvh`) é dividida entre header e a
    // faixa de conteúdo; `overflow-hidden` garante que a página nunca rola —
    // quem rola é só o `<main>` (design-system). `h-dvh` (e não `h-screen`)
    // porque no mobile a barra do navegador entra/sai e `100vh` corta conteúdo.
    <div className="flex h-dvh w-full flex-col overflow-hidden">
      {/* Em 390px este header transbordava e o botão "Sair" ficava CORTADO fora
          da viewport (visto no screenshot mobile de todos os perfis). O título
          encolhe (`min-w-0` + `truncate`), o e-mail some abaixo de `sm` — não é
          acionável, e o papel basta para a pessoa saber em que contexto está —
          e o grupo da direita é `shrink-0`, então "Sair" nunca some. */}
      <header className="bg-card flex shrink-0 items-center justify-between gap-3 border-b px-4 py-3 sm:px-6">
        <div className="min-w-0 truncate font-semibold">Auditoria de Lançamentos</div>
        <div className="flex shrink-0 items-center gap-2 sm:gap-4">
          <NotificationBell />
          <span className="text-muted-foreground flex min-w-0 items-center text-sm">
            <span className="hidden max-w-[16rem] truncate sm:inline">{user.email}</span>
            <span className="hidden px-2 sm:inline" aria-hidden="true">
              ·
            </span>
            {/* Rótulo PT-BR da matriz, nunca o enum cru: com os papéis da S5 o
                `capitalize` do valor exibia "Client_manager". */}
            <span className="truncate">{roleLabel(user)}</span>
          </span>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            <LogOut className="h-4 w-4" aria-hidden="true" />
            Sair
          </Button>
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        {/* Abaixo de `md` o aside não existe — no contexto de cliente os chips
            do `ClientShell` (mesma árvore, `nav-items`) cobrem a navegação até
            a task do drawer mobile (86e2n4pf9). */}
        <aside className="bg-card/50 hidden w-56 shrink-0 overflow-y-auto border-r p-4 md:block">
          <SidebarNav user={user} />
        </aside>
        {/* ÚNICO elemento com rolagem — `min-w-0` evita que uma tabela larga
            empurre o shell e reintroduza scroll horizontal na página. */}
        <main className="min-w-0 flex-1 overflow-y-auto p-6">{children}</main>
      </div>
      {/* Observa a navegação para emitir `autor_navegou_fora` (não renderiza
          nada). Vive no shell porque precisa sobreviver à troca de rota. */}
      <NavigationOutcomeTracker />
    </div>
  );
}

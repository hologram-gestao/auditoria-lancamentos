/**
 * Trava de regressão do `serious/aria-hidden-focus` do sino — escrita pelo QA.
 *
 * **Por que este arquivo existe, ao lado do teste de a11y que já roda:** o
 * `assertNoA11yViolations` (axe em jsdom) NÃO pega este defeito. Verificado por
 * mutação: reintroduzindo o modo modal do Radix (removendo `modal={false}` de
 * `notification-bell.tsx`), a suíte do sino continua **verde** — o axe precisa
 * de layout/cor computados para decidir se o nó escondido é focável e, sem
 * isso, devolve `incomplete`, que não entra em `violations`. O defeito só
 * aparecia no Playwright, no fim da esteira.
 *
 * A checagem aqui é DOM puro, e por isso funciona em jsdom: com o popover
 * aberto, nenhum elemento marcado `aria-hidden="true"` pode conter algo
 * focável. No modo modal o Radix chama `hideOthers()` e marca o resto da
 * página como escondido para a tecnologia assistiva **sem** removê-la da ordem
 * de tabulação — quem navega por teclado sai do popover e cai em controles que
 * o leitor de tela não anuncia ("foco no vazio").
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api/usage-events', () => ({
  recordNotificacaoEntregue: vi.fn(),
}));

const countState = { data: 1 as number | undefined };
const listState = {
  data: {
    pages: [
      {
        data: [
          {
            id: 'n1',
            session_id: 'sess-1',
            client_id: 'cli-1',
            tipo: 'processada',
            omie_conta_id: 42,
            reference_month: '2026-06-01',
            error_code: null,
            read_at: null,
            created_at: '2026-07-26T12:00:00.000Z',
          },
        ],
      },
    ],
  } as { pages: { data: unknown[] }[] } | undefined,
  isLoading: false,
  isError: false,
  hasNextPage: false,
  isFetchingNextPage: false,
  fetchNextPage: vi.fn(),
};

vi.mock('@/hooks/use-notifications', () => ({
  useUnreadNotificationsCount: () => countState,
  useInfiniteNotifications: () => listState,
  useMarkAllNotificationsRead: () => ({ mutate: vi.fn(), isPending: false }),
  useMarkNotificationRead: () => ({ mutate: vi.fn() }),
}));

import { NotificationBell } from '@/components/features/notifications/notification-bell';

const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';

/** Elementos focáveis que estão dentro de uma subárvore `aria-hidden="true"`. */
function focusableInsideAriaHidden(): string[] {
  const found: string[] = [];
  for (const hidden of Array.from(document.querySelectorAll('[aria-hidden="true"]'))) {
    for (const node of Array.from(hidden.querySelectorAll(FOCUSABLE))) {
      const el = node as HTMLElement;
      if (el.hasAttribute('disabled')) continue;
      found.push(
        `${el.tagName.toLowerCase()}${el.getAttribute('aria-label') !== null ? `[${el.getAttribute('aria-label')}]` : ''} dentro de <${hidden.tagName.toLowerCase()} aria-hidden>`,
      );
    }
  }
  return found;
}

/** O chrome que a página real tem ao lado do sino (sidebar + busca no header). */
function renderWithChrome() {
  return render(
    <div>
      <aside>
        <a href="/clientes">Clientes</a>
      </aside>
      <header>
        <input aria-label="Buscar" />
        <NotificationBell />
      </header>
      <main>
        <button type="button">Criar conciliação</button>
      </main>
    </div>,
  );
}

describe('Sino — aria-hidden-focus (trava do QA)', () => {
  beforeEach(() => {
    countState.data = 1;
  });

  it('fechado: nada focável está escondido do leitor de tela', () => {
    renderWithChrome();
    expect(focusableInsideAriaHidden()).toEqual([]);
  });

  it('ABERTO: não esconde o resto da página (modal={false}) — nada focável sob aria-hidden', async () => {
    const user = userEvent.setup();
    renderWithChrome();

    await user.click(screen.getByRole('button', { name: /Notificações/ }));
    await screen.findByRole('menu');

    // Falha com a lista dos nós exatos — é o mesmo par (nó escondido + nó
    // focável) que o axe reporta em `aria-hidden-focus`.
    expect(focusableInsideAriaHidden()).toEqual([]);
  });
});

/**
 * Testes do sino de notificações (FRONT 04.7 / R4).
 *
 * Critérios cobertos:
 *   - contador de não lidas com anúncio acessível (`role="status"`);
 *   - clicar num item marca como lida e leva ao detalhe da conciliação;
 *   - o texto NÃO contém PII do arquivo — só conta/mês/status, e o erro sai
 *     como CÓDIGO (nunca "token" nem a mensagem interna);
 *   - `notificacao_entregue` é emitido ao a pessoa ABRIR o sino (com `via` e
 *     `latencia_s`), e só para as não lidas;
 *   - estados vazio/erro; axe-core sem violações critical/serious.
 *
 * A cadência de 15 s (`refetchInterval`) e o `refetchIntervalInBackground:
 * false` são configuração do TanStack, testados em `use-notifications` por
 * inspeção do hook — aqui os hooks são mockados para isolar a UI.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const pushMock = vi.fn();
const markReadMock = vi.fn();
const recordDeliveredMock = vi.fn();

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: pushMock }) }));

vi.mock('@/lib/api/usage-events', () => ({
  recordNotificacaoEntregue: (...args: unknown[]) => recordDeliveredMock(...args),
}));

const countState = { data: 0 as number | undefined };
const listState = {
  data: undefined as { data: unknown[] } | undefined,
  isLoading: false,
  isError: false,
};

vi.mock('@/hooks/use-notifications', () => ({
  useUnreadNotificationsCount: () => countState,
  useNotifications: () => listState,
  useMarkNotificationRead: () => ({ mutate: markReadMock }),
}));

import { NotificationBell } from '@/components/features/notifications/notification-bell';
import { assertNoA11yViolations } from '@/test/a11y';

function notification(over: Record<string, unknown> = {}) {
  return {
    id: 'n1',
    session_id: 'sess-1',
    client_id: 'cli-1',
    tipo: 'processada',
    omie_conta_id: 42,
    reference_month: '2026-06-01',
    error_code: null,
    read_at: null,
    created_at: new Date(Date.now() - 120_000).toISOString(),
    ...over,
  };
}

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  countState.data = 2;
  listState.data = { data: [notification()] };
  listState.isLoading = false;
  listState.isError = false;
});

describe('NotificationBell — contador', () => {
  it('mostra o número de não lidas e o anuncia de forma acessível', () => {
    render(<NotificationBell />);
    expect(screen.getByRole('button', { name: 'Notificações — 2 não lidas' })).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('2 notificações não lidas');
  });

  it('sem não lidas, não mostra badge e o anúncio muda', () => {
    countState.data = 0;
    render(<NotificationBell />);
    expect(screen.getByRole('button', { name: 'Notificações — nenhuma não lida' })).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('Nenhuma notificação não lida');
  });
});

describe('NotificationBell — lista', () => {
  it('texto de sucesso traz conta e mês, sem PII do arquivo', async () => {
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.click(screen.getByRole('button', { name: /Notificações/ }));

    const menu = await screen.findByRole('menu');
    expect(
      within(menu).getByText('Conciliação de Conta #42 — Junho de 2026 processada. Clique para revisar.'),
    ).toBeVisible();
  });

  it('texto de erro traz o CÓDIGO, nunca a linguagem interna', async () => {
    const user = userEvent.setup();
    listState.data = {
      data: [notification({ tipo: 'erro', error_code: 'RECONCILIATION_TIMEOUT' })],
    };
    render(<NotificationBell />);
    await user.click(screen.getByRole('button', { name: /Notificações/ }));

    expect(await screen.findByText(/cód\. RECONCILIATION_TIMEOUT/)).toBeVisible();
    expect(screen.queryByText(/token/i)).toBeNull();
  });

  it('clicar marca como lida e leva ao detalhe da conciliação', async () => {
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.click(screen.getByRole('button', { name: /Notificações/ }));
    await user.click(await screen.findByText(/processada\. Clique para revisar/));

    expect(markReadMock).toHaveBeenCalledWith('n1');
    expect(pushMock).toHaveBeenCalledWith('/clientes/cli-1/conciliacao/sess-1');
  });

  it('estado vazio e estado de erro são tratados', async () => {
    const user = userEvent.setup();
    listState.data = { data: [] };
    const { unmount } = render(<NotificationBell />);
    await user.click(screen.getByRole('button', { name: /Notificações/ }));
    expect(await screen.findByText('Nenhuma notificação por aqui.')).toBeVisible();
    unmount();

    listState.data = undefined;
    listState.isError = true;
    render(<NotificationBell />);
    await user.click(screen.getByRole('button', { name: /Notificações/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Não foi possível carregar as notificações.',
    );
  });
});

describe('NotificationBell — instrumentação', () => {
  it('emite notificacao_entregue ao abrir o sino, com via e latência', async () => {
    const user = userEvent.setup();
    render(<NotificationBell />);
    expect(recordDeliveredMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /Notificações/ }));

    await waitFor(() => expect(recordDeliveredMock).toHaveBeenCalledTimes(1));
    const [sessionId, via, latency] = recordDeliveredMock.mock.calls[0] as [string, string, number];
    expect(sessionId).toBe('sess-1');
    expect(via).toBe('sino');
    // ~120 s de espera até ser vista.
    expect(latency).toBeGreaterThanOrEqual(119);
    expect(latency).toBeLessThan(125);
  });

  it('não emite para notificação já lida', async () => {
    const user = userEvent.setup();
    listState.data = { data: [notification({ read_at: new Date().toISOString() })] };
    render(<NotificationBell />);
    await user.click(screen.getByRole('button', { name: /Notificações/ }));
    await screen.findByRole('menu');
    expect(recordDeliveredMock).not.toHaveBeenCalled();
  });
});

describe('NotificationBell — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(<NotificationBell />);
    await assertNoA11yViolations(container);
  });
});

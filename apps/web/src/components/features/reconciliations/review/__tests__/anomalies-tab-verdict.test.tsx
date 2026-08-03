/**
 * Testes do veredito do revisor na aba Anomalias (FRONT 06.7 / R4).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - o operador marca procedente/improcedente e o PATCH manda **só**
 *     `review_verdict` (omitir `resolved` = "não mexa nele");
 *   - os TRÊS estados são distinguíveis: "Não avaliado" ≠ "Procedente" ≠
 *     "Improcedente", com `aria-pressed` carregando o estado (não só a cor);
 *   - tipo que o servidor não aceita julgar (`padrao_quebrado`, estruturais)
 *     não ganha a ação — em vez de um botão que devolveria 400;
 *   - reenviar o mesmo veredito não dispara request;
 *   - erro do servidor vira mensagem legível em PT-BR (nunca `err.message`);
 *   - botões desabilitados durante a mutação;
 *   - papel sem `review_export` não vê a ação, mas continua LENDO o veredito;
 *   - axe-core sem violações `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const listState = {
  data: undefined as { data: AnomalyItem[]; pagination: Record<string, number> } | undefined,
  isLoading: false,
};

const patchMock = vi.fn();
const patchState = { mutateAsync: patchMock, isPending: false };

vi.mock('@/hooks/use-reconciliations', () => ({
  useAnomalies: () => listState,
  usePatchAnomaly: () => patchState,
  useAnomalyTypes: () => ({ data: [], isLoading: false }),
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

const toastErrorMock = vi.fn();
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: (...args: unknown[]) => toastErrorMock(...args) },
}));

// Imports do SUT DEPOIS dos `vi.mock`.
import { AnomaliesTab } from '@/components/features/reconciliations/review/anomalies-tab';
import { ApiError } from '@/lib/api/client';
import type { AnomalyItem } from '@/lib/api/reconciliations';
import type { AuthenticatedUser } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const SESSION_ID = 's1';

function anomaly(over: Partial<AnomalyItem> = {}): AnomalyItem {
  return {
    id: 'a1',
    anomaly_type: {
      id: 't1',
      code: 'qualificacao_suspeita',
      name: 'Classificação suspeita',
      severity: 'moderate',
    },
    detected_by: 'ai',
    resolved: false,
    review_verdict: null,
    context: 'IOF classificado como juros.',
    resolution_note: null,
    created_at: '2026-07-01T12:00:00Z',
    related_file_entry: null,
    related_omie_entry: null,
    ...over,
  };
}

function actor(over: Partial<AuthenticatedUser> = {}): AuthenticatedUser {
  return {
    id: 'op',
    email: 'operador@cliente-exemplo.com.br',
    name: 'Operador do Cliente',
    role: 'client_operator',
    scope: 'client',
    client_id: 'c1',
    ...over,
  };
}

function setList(rows: AnomalyItem[]) {
  listState.data = {
    data: rows,
    pagination: { page: 1, pageSize: 20, total: rows.length, totalPages: 1 },
  };
}

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  authState.user = actor();
  listState.isLoading = false;
  patchState.isPending = false;
  setList([anomaly()]);
});

describe('Anomalias — veredito do revisor', () => {
  it('marca improcedente mandando SÓ review_verdict (resolved fica intocado)', async () => {
    const ui = userEvent.setup();
    render(<AnomaliesTab sessionId={SESSION_ID} />);

    await ui.click(
      screen.getByRole('button', { name: 'Marcar "Classificação suspeita" como improcedente' }),
    );

    await waitFor(() => expect(patchMock).toHaveBeenCalledTimes(1));
    expect(patchMock).toHaveBeenCalledWith({
      anomalyId: 'a1',
      payload: { review_verdict: 'improcedente' },
    });
    // O eixo "resolvida" não pode viajar junto — misturar os dois apagaria a
    // métrica de outcome da sprint.
    const payload = patchMock.mock.calls[0]?.[0] as { payload: Record<string, unknown> };
    expect(payload.payload).not.toHaveProperty('resolved');
    expect(payload.payload).not.toHaveProperty('resolution_note');
  });

  it('distingue "não avaliado" de um veredito já marcado', () => {
    setList([
      anomaly({ id: 'a1', review_verdict: null }),
      anomaly({ id: 'a2', review_verdict: 'procedente' }),
      anomaly({ id: 'a3', review_verdict: 'improcedente' }),
    ]);
    render(<AnomaliesTab sessionId={SESSION_ID} />);

    // `selector: 'span'` separa o INDICADOR DE ESTADO dos botões, que carregam
    // o mesmo rótulo. Sem isso o teste passaria só por existirem os botões —
    // ou seja, sem provar que o estado é visível.
    expect(screen.getByText('Não avaliado', { selector: 'span' })).toBeVisible();
    expect(screen.getByText('Procedente', { selector: 'span' })).toBeVisible();
    expect(screen.getByText('Improcedente', { selector: 'span' })).toBeVisible();
  });

  it('o estado vai em aria-pressed, não só na cor', () => {
    setList([anomaly({ review_verdict: 'procedente' })]);
    render(<AnomaliesTab sessionId={SESSION_ID} />);

    expect(
      screen.getByRole('button', { name: 'Marcar "Classificação suspeita" como procedente' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(
      screen.getByRole('button', { name: 'Marcar "Classificação suspeita" como improcedente' }),
    ).toHaveAttribute('aria-pressed', 'false');
  });

  it('reenviar o MESMO veredito não dispara request', async () => {
    const ui = userEvent.setup();
    setList([anomaly({ review_verdict: 'procedente' })]);
    render(<AnomaliesTab sessionId={SESSION_ID} />);

    await ui.click(
      screen.getByRole('button', { name: 'Marcar "Classificação suspeita" como procedente' }),
    );
    expect(patchMock).not.toHaveBeenCalled();
  });

  it('só flag da Camada 1 recebe a ação — os demais tipos não', () => {
    setList([
      anomaly({
        id: 'a9',
        anomaly_type: {
          id: 't9',
          code: 'padrao_quebrado',
          name: 'Padrão quebrado',
          severity: 'info',
        },
      }),
    ]);
    render(<AnomaliesTab sessionId={SESSION_ID} />);

    expect(
      screen.queryByRole('button', { name: /Marcar "Padrão quebrado" como/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('Não avaliado')).not.toBeInTheDocument();
  });

  it('incoerência também é julgável (o outro código da Camada 1)', () => {
    setList([
      anomaly({
        anomaly_type: {
          id: 't2',
          code: 'qualificacao_incoerente',
          name: 'Classificação incoerente',
          severity: 'critical',
        },
      }),
    ]);
    render(<AnomaliesTab sessionId={SESSION_ID} />);
    expect(
      screen.getByRole('button', { name: 'Marcar "Classificação incoerente" como procedente' }),
    ).toBeVisible();
  });

  it('erro do servidor vira mensagem legível, nunca a interna', async () => {
    const ui = userEvent.setup();
    patchMock.mockRejectedValueOnce(
      new ApiError(400, {
        code: 'VALIDATION_ERROR',
        message: "Anomalia de tipo 'x' não aceita veredito de revisão.",
        userMessage:
          'Só é possível marcar como procedente ou improcedente uma suspeita levantada pela análise de classificação.',
      }),
    );
    render(<AnomaliesTab sessionId={SESSION_ID} />);

    await ui.click(
      screen.getByRole('button', { name: 'Marcar "Classificação suspeita" como procedente' }),
    );

    await waitFor(() => expect(toastErrorMock).toHaveBeenCalledTimes(1));
    expect(toastErrorMock).toHaveBeenCalledWith(
      'Só é possível marcar como procedente ou improcedente uma suspeita levantada pela análise de classificação.',
    );
  });

  it('durante a mutação os botões ficam desabilitados', () => {
    patchState.isPending = true;
    render(<AnomaliesTab sessionId={SESSION_ID} />);

    expect(
      screen.getByRole('button', { name: 'Marcar "Classificação suspeita" como procedente' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Marcar "Classificação suspeita" como improcedente' }),
    ).toBeDisabled();
  });
});

describe('Anomalias — gating pela matriz (review_export)', () => {
  it('todos os papéis da matriz podem julgar', () => {
    for (const role of ['admin', 'manager', 'client_manager', 'client_operator'] as const) {
      authState.user = actor({ role });
      const view = render(<AnomaliesTab sessionId={SESSION_ID} />);
      expect(
        screen.getByRole('button', { name: 'Marcar "Classificação suspeita" como procedente' }),
      ).toBeVisible();
      view.unmount();
    }
  });

  it('sem usuário na sessão a ação some, mas o veredito continua legível', () => {
    authState.user = null;
    setList([anomaly({ review_verdict: 'improcedente' })]);
    render(<AnomaliesTab sessionId={SESSION_ID} />);

    expect(
      screen.queryByRole('button', { name: /Marcar "Classificação suspeita" como/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Improcedente', { selector: 'span' })).toBeVisible();
  });
});

describe('Anomalias — sem regressão nas colunas existentes', () => {
  it('mantém severidade, tipo, detectado por e status', () => {
    setList([anomaly({ resolved: true, resolution_note: 'Ajustado no Omie.' })]);
    render(<AnomaliesTab sessionId={SESSION_ID} />);

    const row = screen.getByRole('row', { name: /Classificação suspeita/ });
    expect(within(row).getByText('Sistema')).toBeVisible();
    expect(within(row).getByText('Resolvida')).toBeVisible();
    expect(within(row).getByText(/Ajustado no Omie/)).toBeVisible();
  });

  it('lista vazia continua com a mensagem de sempre (colSpan acompanhou a coluna nova)', () => {
    setList([]);
    render(<AnomaliesTab sessionId={SESSION_ID} />);
    expect(screen.getByText('Nenhuma anomalia registrada.')).toBeVisible();
  });
});

describe('Anomalias — acessibilidade', () => {
  it('não tem violações critical/serious com os três estados de veredito', async () => {
    setList([
      anomaly({ id: 'a1', review_verdict: null }),
      anomaly({ id: 'a2', review_verdict: 'procedente' }),
      anomaly({ id: 'a3', review_verdict: 'improcedente' }),
    ]);
    const { container } = render(<AnomaliesTab sessionId={SESSION_ID} />);
    await assertNoA11yViolations(container);
  });
});

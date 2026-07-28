/**
 * Testes do emissor de `autor_navegou_fora` (FRONT 04.6 / instrumentação).
 *
 * Este é o evento que **prova o outcome** da sprint, então o que ele NÃO conta
 * importa tanto quanto o que ele conta:
 *   - ficar na rota onde criou não é "navegar fora";
 *   - abrir o DETALHE daquela conciliação para ver o progresso também não é —
 *     é exatamente o "esperar olhando" que a sprint quer substituir. Contar
 *     isso inflaria a métrica e mediria a máquina, não a pessoa.
 */
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const recordMock = vi.fn();
let pathname = '/clientes/c1';

vi.mock('next/navigation', () => ({ usePathname: () => pathname }));
vi.mock('@/lib/api/usage-events', () => ({
  recordAutorNavegouFora: (...args: unknown[]) => recordMock(...args),
}));

import { NavigationOutcomeTracker } from '@/components/features/reconciliations/create/navigation-outcome-tracker';
import { usePendingCreations } from '@/stores/pending-creations';

function trackCreation(originPath = '/clientes/c1') {
  usePendingCreations.getState().track({
    sessionId: 's1',
    clientId: 'c1',
    createdAtMs: Date.now() - 42_000,
    originPath,
  });
}

beforeEach(() => {
  recordMock.mockClear();
  recordMock.mockResolvedValue(true);
  usePendingCreations.setState({ pending: {} });
  pathname = '/clientes/c1';
});

afterEach(() => {
  usePendingCreations.setState({ pending: {} });
});

describe('NavigationOutcomeTracker', () => {
  it('não emite enquanto a pessoa está na rota onde criou', () => {
    trackCreation();
    render(<NavigationOutcomeTracker />);
    expect(recordMock).not.toHaveBeenCalled();
  });

  it('emite com os segundos decorridos ao sair para outra rota', async () => {
    trackCreation();
    pathname = '/clientes';
    render(<NavigationOutcomeTracker />);

    await waitFor(() => expect(recordMock).toHaveBeenCalledTimes(1));
    const [sessionId, seconds] = recordMock.mock.calls[0] as [string, number];
    expect(sessionId).toBe('s1');
    // ~42 s desde a criação (tolerância para o tempo de execução do teste).
    expect(seconds).toBeGreaterThanOrEqual(41);
    expect(seconds).toBeLessThan(45);
  });

  it('NÃO emite quando a pessoa vai ver o progresso da própria conciliação', () => {
    trackCreation();
    pathname = '/clientes/c1/conciliacao/s1';
    render(<NavigationOutcomeTracker />);
    expect(recordMock).not.toHaveBeenCalled();
  });

  it('dá baixa na criação após emitir (não repete a cada render)', async () => {
    trackCreation();
    pathname = '/configuracoes/usuarios';
    const { rerender } = render(<NavigationOutcomeTracker />);
    await waitFor(() => expect(recordMock).toHaveBeenCalledTimes(1));

    rerender(<NavigationOutcomeTracker />);
    expect(recordMock).toHaveBeenCalledTimes(1);
    expect(usePendingCreations.getState().pending).toEqual({});
  });
});

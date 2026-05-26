/**
 * Testes do dialog de override manual (S19 FRONT 12.2).
 *
 * Mockamos `usePatchAnomaly` e o sonner `toast` pra rodar isolado.
 * A interação com TanStack Query é coberta em E2E em S18.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import { QualificationOverrideDialog } from '@/components/features/reconciliations/review/qualification-override-dialog';
import { ApiError } from '@/lib/api/client';
import type { AnomalyItem } from '@/lib/api/reconciliations';

const mutateAsyncMock: Mock = vi.fn();
const toastSuccess: Mock = vi.fn();
const toastError: Mock = vi.fn();

vi.mock('@/hooks/use-reconciliations', () => ({
  usePatchAnomaly: () => ({
    mutateAsync: mutateAsyncMock,
    isPending: false,
  }),
}));

vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

function makeAnomaly(overrides: Partial<AnomalyItem> & { code?: string }): AnomalyItem {
  const code = overrides.code ?? 'qualificacao_suspeita';
  return {
    id: `anomaly-${code}`,
    anomaly_type: {
      id: 't-1',
      code,
      name:
        code === 'qualificacao_incoerente'
          ? 'Qualificação incoerente (IA)'
          : 'Qualificação suspeita (IA)',
      severity: code === 'qualificacao_incoerente' ? 'critical' : 'moderate',
    },
    detected_by: 'ai',
    resolved: false,
    context: 'Motivo do sinal',
    resolution_note: null,
    created_at: '2026-05-26T12:00:00Z',
    related_file_entry: null,
    related_omie_entry: null,
    ...overrides,
  };
}

const entry = {
  transaction_date: '2026-04-15',
  description: 'Pagamento fornecedor X',
  amount: '-150.50',
};

beforeEach(() => {
  mutateAsyncMock.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('QualificationOverrideDialog', () => {
  it('mostra erro Zod quando a justificativa tem menos de 10 chars', async () => {
    render(
      <QualificationOverrideDialog
        sessionId="s1"
        anomalies={[makeAnomaly({})]}
        entry={entry}
        open
        onOpenChange={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('Justificativa'), { target: { value: 'curto' } });
    fireEvent.click(screen.getByRole('button', { name: /marcar como ok manualmente/i }));

    await waitFor(() => {
      expect(screen.getByText(/pelo menos 10 caracteres/i)).toBeInTheDocument();
    });
    expect(mutateAsyncMock).not.toHaveBeenCalled();
  });

  it('dispara PATCH com payload correto e toast de sucesso (1 anomalia)', async () => {
    mutateAsyncMock.mockResolvedValue({});
    const onOpenChange = vi.fn();

    render(
      <QualificationOverrideDialog
        sessionId="s1"
        anomalies={[makeAnomaly({ id: 'anomaly-1' })]}
        entry={entry}
        open
        onOpenChange={onOpenChange}
      />,
    );
    fireEvent.change(screen.getByLabelText('Justificativa'), {
      target: { value: 'O fornecedor mudou de razao social, conferi com o ERP.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /marcar como ok manualmente/i }));

    await waitFor(() => {
      expect(mutateAsyncMock).toHaveBeenCalledTimes(1);
    });
    expect(mutateAsyncMock).toHaveBeenCalledWith({
      anomalyId: 'anomaly-1',
      payload: {
        resolved: true,
        resolution_note: 'O fornecedor mudou de razao social, conferi com o ERP.',
      },
    });
    expect(toastSuccess).toHaveBeenCalledWith('Anomalia marcada como ok manualmente.');
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('dispara PATCH para cada anomalia em paralelo e usa mensagem plural', async () => {
    mutateAsyncMock.mockResolvedValue({});
    render(
      <QualificationOverrideDialog
        sessionId="s1"
        anomalies={[
          makeAnomaly({ id: 'a1', code: 'qualificacao_suspeita' }),
          makeAnomaly({ id: 'a2', code: 'qualificacao_incoerente' }),
        ]}
        entry={entry}
        open
        onOpenChange={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('Justificativa'), {
      target: { value: 'Verificado com o operador da conciliacao.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /marcar como ok manualmente/i }));

    await waitFor(() => {
      expect(mutateAsyncMock).toHaveBeenCalledTimes(2);
    });
    expect(toastSuccess).toHaveBeenCalledWith('2 anomalias marcadas como ok manualmente.');
  });

  it('mostra toast de erro com userMessage do ApiError em falha total', async () => {
    const err = new ApiError(422, {
      code: 'INVALID_FIELD',
      message: 'Nota inválida',
      userMessage: 'Justifique melhor a resolução.',
    });
    mutateAsyncMock.mockRejectedValue(err);

    render(
      <QualificationOverrideDialog
        sessionId="s1"
        anomalies={[makeAnomaly({ id: 'a1' })]}
        entry={entry}
        open
        onOpenChange={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('Justificativa'), {
      target: { value: 'Justificativa razoavel com mais de dez caracteres.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /marcar como ok manualmente/i }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Justifique melhor a resolução.');
    });
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('lista as anomalias com nome do tipo no corpo do dialog', () => {
    render(
      <QualificationOverrideDialog
        sessionId="s1"
        anomalies={[
          makeAnomaly({ id: 'a1', code: 'qualificacao_suspeita' }),
          makeAnomaly({ id: 'a2', code: 'qualificacao_incoerente' }),
        ]}
        entry={entry}
        open
        onOpenChange={vi.fn()}
      />,
    );
    expect(screen.getByText('Qualificação suspeita (IA)')).toBeInTheDocument();
    expect(screen.getByText('Qualificação incoerente (IA)')).toBeInTheDocument();
    expect(screen.getAllByText('Motivo do sinal').length).toBeGreaterThan(0);
  });
});

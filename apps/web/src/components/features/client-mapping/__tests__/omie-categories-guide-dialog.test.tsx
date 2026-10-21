/**
 * O diálogo "Guia para configurar no Omie" (86e3qhht4).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 *
 * Cobre: os quatro comportamentos com "Gerar provisão e baixa" pré-selecionado, o
 * download com o comportamento escolhido (arquivo, toast, fecha), o erro do servidor
 * em toast com o `userMessage` (o diálogo fica aberto) e axe sem `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const toastSuccess = vi.fn();
const toastError = vi.fn();
const downloadSpy = vi.fn();
vi.mock('@/lib/download', () => ({
  triggerBrowserDownload: (...args: unknown[]) => downloadSpy(...args),
}));
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

const exportState = { mutateAsync: vi.fn(), isPending: false };
vi.mock('@/hooks/use-client-mapping', () => ({
  useExportOmieCategoriesGuide: () => exportState,
}));

import { OmieCategoriesGuideAction } from '@/components/features/client-mapping/omie-categories-guide-dialog';
import { ApiError } from '@/lib/api/client';
import { OMIE_CATEGORY_BEHAVIOR_LABELS } from '@/lib/validation/omie-categories-guide';
import { assertNoA11yViolations } from '@/test/a11y';

async function openDialog() {
  const user = userEvent.setup();
  render(<OmieCategoriesGuideAction clientId="c1" />);
  await user.click(screen.getByRole('button', { name: /Guia para configurar no Omie/ }));
  return { user, dialog: await screen.findByRole('dialog') };
}

beforeEach(() => {
  exportState.mutateAsync = vi.fn();
  exportState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
  downloadSpy.mockReset();
});

describe('OmieCategoriesGuideAction', () => {
  it('explica os três blocos e oferece os quatro comportamentos, provisão e baixa marcado', async () => {
    const { dialog } = await openDialog();
    expect(within(dialog).getByText('Configurar:')).toBeVisible();
    expect(within(dialog).getByText('Não gerar lançamento:')).toBeVisible();
    expect(within(dialog).getByText('Sem decisão:')).toBeVisible();
    const group = within(dialog).getByRole('group', { name: 'Comportamento padrão na Omie' });
    const radios = within(group).getAllByRole('radio');
    expect(radios.map((r) => r.closest('label')?.textContent)).toEqual(
      Object.values(OMIE_CATEGORY_BEHAVIOR_LABELS),
    );
    expect(within(group).getByRole('radio', { name: 'Gerar provisão e baixa' })).toBeChecked();
  });

  it('baixar: manda o comportamento escolhido, dispara o arquivo, avisa e fecha', async () => {
    const blob = new Blob(['x']);
    exportState.mutateAsync = vi
      .fn()
      .mockResolvedValue({ blob, filename: 'guia-omie-1234abcd.xlsx' });
    const { user, dialog } = await openDialog();
    await user.click(
      within(dialog).getByRole('radio', { name: 'Gerar só a baixa, pela conta de resultado' }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Baixar guia' }));
    await waitFor(() =>
      expect(exportState.mutateAsync).toHaveBeenCalledWith('baixa_conta_resultado'),
    );
    expect(downloadSpy).toHaveBeenCalledWith(blob, 'guia-omie-1234abcd.xlsx');
    expect(toastSuccess).toHaveBeenCalledWith(
      'Guia baixado. Abra ao lado da tela de categorias da Omie.',
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('erro do servidor vira toast com o userMessage e o diálogo fica aberto', async () => {
    exportState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(409, {
        code: 'CAPACIDADE_AUSENTE',
        message: 'x',
        userMessage: 'A origem deste cliente é por arquivo.',
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Baixar guia' }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('A origem deste cliente é por arquivo.'),
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(downloadSpy).not.toHaveBeenCalled();
  });

  it('a11y: diálogo aberto sem critical/serious', async () => {
    const { dialog } = await openDialog();
    await assertNoA11yViolations(dialog);
  });
});

/**
 * O diálogo "Gerar planilha da Omie" (86e3qhhrr).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 *
 * Cobre: a prévia (contagens, conferência do cadastro no Omie nos três estados),
 * os problemas que impedem a geração com os CÓDIGOS das contas e sem o botão de
 * baixar, o download (arquivo, toast e o próximo passo para o guia do de-para), a recusa 409 do download mostrada no
 * diálogo (nunca toast) e axe sem `critical`/`serious`.
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

interface PreviewState {
  data: OmieExportPreview | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
}
const previewState: PreviewState = {
  data: undefined,
  isLoading: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
};
const exportState = { mutateAsync: vi.fn(), isPending: false };
const refreshState = { mutate: vi.fn(), isPending: false };
const previewEnabled = vi.fn();

vi.mock('@/hooks/use-client-accounting-chart', () => ({
  useOmieChartExportPreview: (_clientId: string, options: { enabled: boolean }) => {
    previewEnabled(options.enabled);
    return previewState;
  },
  useExportOmieChart: () => exportState,
  useRefreshOmieChartExportPreview: () => refreshState,
}));

const clientDetailState = {
  data: { connections: [{ provider_type: 'omie' }] } as
    | { connections: { provider_type: string }[] }
    | undefined,
};
vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => clientDetailState,
}));

import { OmieChartExportAction } from '@/components/features/accounting-chart/omie-chart-export-dialog';
import { ApiError } from '@/lib/api/client';
import type { OmieExportPreview } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

function preview(overrides: Partial<OmieExportPreview> = {}): OmieExportPreview {
  return {
    contas: 563,
    vinculadas: 177,
    semVinculo: 386,
    inativas: 0,
    ausentes: 0,
    naturezaManual: 0,
    problems: [],
    omieRegistry: { checked: true, missing: 0, reason: null },
    ...overrides,
  };
}

async function openDialog() {
  const user = userEvent.setup();
  render(<OmieChartExportAction clientId="c1" />);
  await user.click(screen.getByRole('button', { name: 'Gerar planilha da Omie' }));
  return { user, dialog: await screen.findByRole('dialog') };
}

beforeEach(() => {
  previewState.data = preview();
  previewState.isLoading = false;
  previewState.isError = false;
  previewState.error = null;
  exportState.mutateAsync = vi.fn();
  exportState.isPending = false;
  refreshState.mutate = vi.fn();
  refreshState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
  downloadSpy.mockReset();
  previewEnabled.mockReset();
  clientDetailState.data = { connections: [{ provider_type: 'omie' }] };
});

describe('OmieChartExportAction', () => {
  it('a prévia só é pedida com o diálogo aberto', async () => {
    render(<OmieChartExportAction clientId="c1" />);
    expect(previewEnabled).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Gerar planilha da Omie' }));
    await screen.findByRole('dialog');
    expect(previewEnabled).toHaveBeenCalledWith(true);
  });

  it('mostra as contagens e que está pronta para importar', async () => {
    const { dialog } = await openDialog();
    const counts = within(dialog).getByTestId('omie-export-counts');
    expect(counts).toHaveTextContent('Contas563');
    expect(counts).toHaveTextContent('Com CNPJ/CPF177');
    expect(counts).toHaveTextContent('Sem vínculo386');
    expect(within(dialog).getByText(/pronta para importar/)).toBeInTheDocument();
  });

  it('avisa quantos documentos não têm cadastro no Omie e o que acontece com eles', async () => {
    previewState.data = preview({ omieRegistry: { checked: true, missing: 4, reason: null } });
    const { dialog } = await openDialog();
    expect(within(dialog).getByTestId('omie-export-registry-warning')).toHaveTextContent(
      /4 contas têm um CNPJ\/CPF que não está no cadastro do Omie.*ficam como diversos/,
    );
    expect(within(dialog).getByRole('button', { name: 'Baixar planilha' })).toBeEnabled();
  });

  it('"Conferir de novo no Omie" relê o cadastro (refresh)', async () => {
    previewState.data = preview({ omieRegistry: { checked: true, missing: 4, reason: null } });
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Conferir de novo no Omie' }));
    expect(refreshState.mutate).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['sem_omie', /não está conectado ao Omie/],
    ['origem_com_erro', /conexão deste cliente com o Omie está com erro/],
    ['omie_indisponivel', /O Omie não respondeu agora/],
  ] as const)('sem conferência (%s), explica e deixa baixar', async (reason, text) => {
    previewState.data = preview({ omieRegistry: { checked: false, missing: null, reason } });
    const { dialog } = await openDialog();
    expect(within(dialog).getByText(text)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Baixar planilha' })).toBeEnabled();
  });

  it('com problemas, nomeia os códigos e não oferece o download', async () => {
    previewState.data = preview({
      problems: [
        { reason: 'conta_sem_classificacao', accountCodes: ['77', '78'], count: 2 },
        { reason: 'texto_longo', accountCodes: ['9'], count: 1 },
      ],
    });
    const { dialog } = await openDialog();
    const problems = within(dialog).getByTestId('omie-export-problems');
    expect(problems).toHaveTextContent('Contas sem classificação');
    expect(problems).toHaveTextContent('Contas 77, 78');
    expect(problems).toHaveTextContent('Conta 9');
    expect(within(dialog).queryByRole('button', { name: 'Baixar planilha' })).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Fechar' })).toBeInTheDocument();
  });

  it('baixar: dispara o arquivo, avisa e mostra o próximo passo (86e3qhht4)', async () => {
    const blob = new Blob(['x']);
    exportState.mutateAsync = vi
      .fn()
      .mockResolvedValue({ blob, filename: 'Omie_Plano_Contas_1234abcd.xlsx' });
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Baixar planilha' }));
    await waitFor(() =>
      expect(downloadSpy).toHaveBeenCalledWith(blob, 'Omie_Plano_Contas_1234abcd.xlsx'),
    );
    expect(toastSuccess).toHaveBeenCalledWith(
      'Planilha da Omie baixada. Importe no Painel do Contador da Omie.',
    );
    // O diálogo fica aberto com a ponte para o guia das categorias no de-para.
    const nextStep = await within(dialog).findByTestId('omie-export-next-step');
    expect(nextStep).toHaveTextContent(
      'Depois de importar o plano na Omie, configure as categorias com o guia do de-para',
    );
    expect(
      within(nextStep).getByRole('link', { name: /Abrir o de-para na conta contábil/ }),
    ).toHaveAttribute('href', '/clientes/c1/de-para?destination=conta_contabil');
    expect(within(dialog).queryByRole('button', { name: 'Baixar planilha' })).toBeNull();
    await user.click(within(dialog).getByRole('button', { name: 'Fechar' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('sem Omie, o próximo passo não aponta para o guia, que o de-para não oferece', async () => {
    clientDetailState.data = { connections: [{ provider_type: 'arquivo' }] };
    exportState.mutateAsync = vi
      .fn()
      .mockResolvedValue({ blob: new Blob(['x']), filename: 'x.xlsx' });
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Baixar planilha' }));
    const nextStep = await within(dialog).findByTestId('omie-export-next-step');
    expect(nextStep).toHaveTextContent('Planilha baixada');
    expect(within(nextStep).queryByRole('link')).toBeNull();
  });

  it('a recusa 409 do download aparece no diálogo, nunca em toast', async () => {
    exportState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(409, {
        code: 'PLANILHA_OMIE_RECUSADA',
        message: 'x',
        userMessage: 'A planilha da Omie não pode ser gerada.',
        details: {
          problems: [{ reason: 'conta_sem_natureza', accountCodes: ['12'], count: 1 }],
        },
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Baixar planilha' }));
    const problems = await within(dialog).findByTestId('omie-export-problems');
    expect(problems).toHaveTextContent('Contas sem natureza');
    expect(problems).toHaveTextContent('Conta 12');
    expect(toastError).not.toHaveBeenCalled();
    expect(within(dialog).queryByRole('button', { name: 'Baixar planilha' })).toBeNull();
  });

  it('409 sem problema legível vira toast e não libera o download de novo em silêncio', async () => {
    exportState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(409, {
        code: 'PLANILHA_OMIE_RECUSADA',
        message: 'x',
        userMessage: 'A planilha da Omie não pode ser gerada.',
        details: { problems: [{ nada: 1 }] },
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Baixar planilha' }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('A planilha da Omie não pode ser gerada.'),
    );
  });

  it('outro erro do download vira toast com a mensagem do servidor', async () => {
    exportState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(409, {
        code: 'CLIENT_CLOSED',
        message: 'x',
        userMessage: 'Cliente encerrado.',
      }),
    );
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole('button', { name: 'Baixar planilha' }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Cliente encerrado.'));
  });

  it('erro na prévia: mensagem e "Tentar de novo"', async () => {
    previewState.data = undefined;
    previewState.isError = true;
    previewState.error = new ApiError(403, {
      code: 'FORBIDDEN',
      message: 'x',
      userMessage: 'Sem permissão.',
    });
    const { user, dialog } = await openDialog();
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Sem permissão.');
    await user.click(within(dialog).getByRole('button', { name: 'Tentar de novo' }));
    expect(previewState.refetch).toHaveBeenCalled();
  });

  it('diálogo aberto sem violações de a11y', async () => {
    previewState.data = preview({ omieRegistry: { checked: true, missing: 2, reason: null } });
    const { dialog } = await openDialog();
    await assertNoA11yViolations(dialog.ownerDocument.body);
  });
});

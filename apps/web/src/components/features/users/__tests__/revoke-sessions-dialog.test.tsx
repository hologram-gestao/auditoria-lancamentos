/**
 * Diálogo "Encerrar sessões" (86e3anx4u, parte 1).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * O que se prova aqui, sem browser:
 *   - confirma: chama a mutation com o id do alvo, fecha e dá o toast SEM nome
 *     nem e-mail (o toast fica na tela depois de a pessoa sair do foco);
 *   - cancela: nada é chamado e o diálogo fecha;
 *   - erro do servidor: toast com a `userMessage` e o diálogo FICA aberto;
 *   - enviando: Cancelar e a ação ficam desabilitados, e fechar é recusado;
 *   - o corpo diz o que acontece ANTES de confirmar (conta ativa, mesma senha);
 *   - axe-core sem violações critical/serious com o diálogo aberto.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: toastSuccess, error: toastError } }));

import {
  REVOKE_SESSIONS_SUCCESS_TOAST,
  RevokeSessionsDialog,
} from '@/components/features/users/revoke-sessions-dialog';
import { ApiError } from '@/lib/api/client';
import { assertNoA11yViolations } from '@/test/a11y';

const TARGET = { id: 'u1', name: 'Bruna R.', email: 'bruna@hologram.com.br' };

function renderDialog(over: { revoke?: ReturnType<typeof vi.fn>; isPending?: boolean } = {}) {
  const revoke = over.revoke ?? vi.fn().mockResolvedValue(undefined);
  const onOpenChange = vi.fn();
  render(
    <RevokeSessionsDialog
      open
      onOpenChange={onOpenChange}
      target={TARGET}
      revoke={revoke}
      isPending={over.isPending ?? false}
    />,
  );
  return { revoke, onOpenChange };
}

describe('RevokeSessionsDialog', () => {
  beforeEach(() => {
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it('diz o que acontece antes de confirmar: todos os acessos saem, a conta segue ativa, mesma senha', () => {
    renderDialog();
    const dialog = screen.getByRole('alertdialog', { name: 'Encerrar sessões' });
    expect(dialog).toHaveTextContent('Bruna R.');
    expect(dialog).toHaveTextContent('bruna@hologram.com.br');
    expect(dialog).toHaveTextContent(/serão encerrados agora/);
    expect(dialog).toHaveTextContent(/A conta continua ativa/);
    expect(dialog).toHaveTextContent(/entra de novo com a senha atual/);
    // Sem formulário: não há senha a digitar.
    expect(within(dialog).queryByRole('textbox')).toBeNull();
    expect(within(dialog).queryByLabelText(/senha/i)).toBeNull();
  });

  it('confirma: chama a mutation com o id do alvo, fecha e o toast não nomeia ninguém', async () => {
    const ui = userEvent.setup();
    const { revoke, onOpenChange } = renderDialog();
    const dialog = screen.getByRole('alertdialog', { name: 'Encerrar sessões' });

    await ui.click(within(dialog).getByRole('button', { name: 'Encerrar sessões' }));

    await waitFor(() => expect(revoke).toHaveBeenCalledWith('u1'));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(toastSuccess).toHaveBeenCalledWith(REVOKE_SESSIONS_SUCCESS_TOAST);
    expect(REVOKE_SESSIONS_SUCCESS_TOAST).not.toContain('Bruna');
    expect(REVOKE_SESSIONS_SUCCESS_TOAST).not.toContain('@');
    expect(toastError).not.toHaveBeenCalled();
  });

  it('cancela: nada é chamado e o diálogo fecha', async () => {
    const ui = userEvent.setup();
    const { revoke, onOpenChange } = renderDialog();
    const dialog = screen.getByRole('alertdialog', { name: 'Encerrar sessões' });

    await ui.click(within(dialog).getByRole('button', { name: 'Cancelar' }));

    expect(revoke).not.toHaveBeenCalled();
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('erro do servidor vira toast com a userMessage e o diálogo fica aberto (409 da própria sessão, por exemplo)', async () => {
    const ui = userEvent.setup();
    const revoke = vi.fn().mockRejectedValue(
      new ApiError(409, {
        code: 'CONFLICT',
        message: 'x',
        userMessage: 'Esta ação encerra as sessões de OUTRA pessoa.',
      }),
    );
    const { onOpenChange } = renderDialog({ revoke });
    const dialog = screen.getByRole('alertdialog', { name: 'Encerrar sessões' });

    await ui.click(within(dialog).getByRole('button', { name: 'Encerrar sessões' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Esta ação encerra as sessões de OUTRA pessoa.'),
    );
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('Cancelar fica à ESQUERDA da ação, e os dois desabilitam no envio', () => {
    const { onOpenChange } = renderDialog({ isPending: true });
    const dialog = screen.getByRole('alertdialog', { name: 'Encerrar sessões' });
    const buttons = within(dialog).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['Cancelar', 'Encerrar sessões']);
    expect(buttons[0]).toBeDisabled();
    expect(buttons[1]).toBeDisabled();
    // Enviando, fechar pelo Escape é recusado: a request já saiu.
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it('não tem violações critical/serious do axe-core', async () => {
    renderDialog();
    await screen.findByRole('alertdialog');
    await assertNoA11yViolations(document.body);
  });
});

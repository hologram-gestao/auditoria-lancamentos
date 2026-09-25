/**
 * Tela de login — validação do e-mail durante o uso (86e2n39eg).
 *
 * A regra de formato sempre existiu (`loginSchema`); o defeito era o MOMENTO:
 * com `mode: 'onSubmit'` a pessoa só descobria "joao@" inválido ao clicar em
 * Entrar. Aqui se prova o `onTouched` (valida ao sair do campo e, depois do
 * primeiro erro, a cada tecla), que o botão não trava por formato, o caminho
 * do e-mail colado com espaço, e que a resposta de credencial errada continua
 * GENÉRICA (CLAUDE.md §3.9).
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api/client';

const { loginMock, replaceMock } = vi.hoisted(() => ({
  loginMock: vi.fn(),
  replaceMock: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: replaceMock }) }));
vi.mock('@/lib/api/auth', () => ({ login: loginMock }));

import LoginPage from '../page';

const GENERIC = 'E-mail ou senha incorretos.';

function emailInput(): HTMLElement {
  return screen.getByLabelText('E-mail');
}

describe('LoginPage — validação do e-mail (86e2n39eg)', () => {
  beforeEach(() => {
    loginMock.mockReset();
    replaceMock.mockReset();
  });

  it('não acusa erro enquanto a pessoa ainda está digitando pela primeira vez', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(emailInput(), 'joao@');
    expect(screen.queryByText('E-mail inválido.')).not.toBeInTheDocument();
  });

  it('e-mail inválido mostra o erro ao sair do campo, sem clicar em Entrar', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(emailInput(), 'joao@');
    await user.tab();

    expect(await screen.findByText('E-mail inválido.')).toBeInTheDocument();
    expect(loginMock).not.toHaveBeenCalled();
    // O campo anuncia o erro: `aria-invalid` e a mensagem ligada por `aria-describedby`.
    const input = emailInput();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    const message = screen.getByText('E-mail inválido.');
    expect(input.getAttribute('aria-describedby')?.split(' ')).toContain(message.id);
  });

  it('corrigir o e-mail limpa a mensagem enquanto digita', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(emailInput(), 'joao@');
    await user.tab();
    expect(await screen.findByText('E-mail inválido.')).toBeInTheDocument();

    await user.type(emailInput(), 'hologram.com.br');
    await waitFor(() => expect(screen.queryByText('E-mail inválido.')).not.toBeInTheDocument());
    expect(emailInput()).toHaveAttribute('aria-invalid', 'false');
  });

  // Critério de aceite da task, pela tela. Quem remove o espaço aqui é o próprio
  // `<input type="email">` (sanitização do HTML, que o jsdom também faz), então
  // este caso passa até sem o `trim` do schema; a prova do `trim` está em
  // `lib/validation/__tests__/auth.test.ts`.
  it('e-mail colado com espaço no fim entra, e vai sem o espaço', async () => {
    loginMock.mockResolvedValue({ id: 'u1', email: 'ana@hologram.com.br' });
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(emailInput(), '  ana@hologram.com.br  ');
    await user.type(screen.getByLabelText('Senha'), 'segredo-123');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));

    await waitFor(() =>
      expect(loginMock).toHaveBeenCalledWith({
        email: 'ana@hologram.com.br',
        password: 'segredo-123',
      }),
    );
    expect(screen.queryByText('E-mail inválido.')).not.toBeInTheDocument();
  });

  it('formato inválido NÃO trava o botão: deixa clicar e mostra o erro no campo', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(emailInput(), 'joao@');
    await user.type(screen.getByLabelText('Senha'), 'qualquer');

    const entrar = screen.getByRole('button', { name: 'Entrar' });
    expect(entrar).toBeEnabled();
    await user.click(entrar);
    expect(await screen.findByText('E-mail inválido.')).toBeInTheDocument();
    expect(loginMock).not.toHaveBeenCalled();
  });

  it('credencial errada continua com a mensagem genérica de sempre (§3.9)', async () => {
    loginMock.mockRejectedValue(
      new ApiError(401, {
        code: 'INVALID_CREDENTIALS',
        message: 'unauthorized',
        userMessage: GENERIC,
      }),
    );
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(emailInput(), 'ana@hologram.com.br');
    await user.type(screen.getByLabelText('Senha'), 'errada');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(GENERIC);
    expect(replaceMock).not.toHaveBeenCalled();
  });
});

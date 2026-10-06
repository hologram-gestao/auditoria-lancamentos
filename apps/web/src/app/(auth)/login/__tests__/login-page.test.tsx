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

import { login as copy } from '@/components/landing/content';
import { ApiError } from '@/lib/api/client';
import { PRODUCT_TITLE } from '@/lib/brand';

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

  it('diz o que fazer a quem esqueceu a senha, sem revelar se o e-mail existe (86e2u5140)', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    const ajuda = screen.getByTestId('login-help');
    expect(ajuda).toHaveTextContent(
      'Esqueceu a senha? Fale com o administrador da sua conta: ele pode redefini-la para você.',
    );
    // Sem link de "esqueci minha senha": não há fluxo por e-mail (decisão registrada).
    expect(screen.queryByRole('link', { name: /senha/i })).toBeNull();
    // A instrução é a MESMA antes e depois de um login recusado: nada nela
    // depende de o e-mail estar cadastrado.
    const antes = ajuda.textContent;
    await user.type(screen.getByLabelText('E-mail'), 'alguem@exemplo.com');
    await user.type(screen.getByLabelText('Senha'), 'senha-errada');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));
    expect(screen.getByTestId('login-help').textContent).toBe(antes);
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

  it('429 mostra a mensagem do servidor, que sabe a janela do limite (86e3anx10)', async () => {
    const RATE_LIMITED =
      'Muitas tentativas de login com este e-mail. Aguarde 5 minutos e tente novamente.';
    loginMock.mockRejectedValue(
      new ApiError(429, {
        code: 'RATE_LIMITED',
        message: 'rate limited',
        userMessage: RATE_LIMITED,
      }),
    );
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(emailInput(), 'ana@hologram.com.br');
    await user.type(screen.getByLabelText('Senha'), 'qualquer');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(RATE_LIMITED);
    expect(alert).not.toHaveTextContent('1 minuto');
    expect(replaceMock).not.toHaveBeenCalled();
  });
});

/**
 * 86e3h1h75: a tela virou a ponte entre a landing e o sistema. O fluxo acima não muda;
 * aqui se prova o que mudou no visual e no texto.
 */
describe('LoginPage — a ponte entre a landing e o sistema (86e3h1h75)', () => {
  beforeEach(() => {
    loginMock.mockReset();
    replaceMock.mockReset();
  });

  it('título e subtítulo DENTRO do card, com texto neutro (a plataforma é multi-organização)', () => {
    render(<LoginPage />);
    const titulo = screen.getByRole('heading', { level: 1, name: PRODUCT_TITLE });
    const card = titulo.closest('.lp-auth-card');
    expect(card).not.toBeNull();
    expect(card).toHaveClass('max-w-sm', 'p-8');
    expect(screen.getByText(copy.subtitle)).toBeInTheDocument();
    expect(emailInput()).toHaveAttribute('placeholder', copy.emailPlaceholder);
    expect(card?.textContent).not.toMatch(/acesso da Hologram|@hologram\.com\.br/);
  });

  it('o botão Entrar é o verde da marca, e desabilitado ele só apaga (nunca cinza sólido)', () => {
    render(<LoginPage />);
    const entrar = screen.getByRole('button', { name: 'Entrar' });
    expect(entrar).toBeDisabled();
    expect(entrar).toHaveClass('bg-brand', 'text-brand-foreground', 'disabled:opacity-50');
    expect(entrar).not.toHaveClass('bg-primary');
  });

  it('no erro, só a mensagem fica vermelha (com ícone); o rótulo segue na cor do texto', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(emailInput(), 'joao@');
    await user.tab();

    const mensagem = await screen.findByText('E-mail inválido.');
    expect(mensagem).toHaveClass('text-destructive');
    expect(mensagem.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    const rotulo = screen.getByText('E-mail', { selector: 'label' });
    expect(rotulo).toHaveClass('text-foreground');
    expect(rotulo).not.toHaveClass('text-destructive');
  });
});

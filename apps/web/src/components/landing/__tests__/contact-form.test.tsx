/**
 * Formulário de contato da landing (86e3fr9vz).
 *
 *   - o honeypot fica fora da árvore acessível e fora do Tab, e vai no payload;
 *   - sem consentimento, nada é enviado;
 *   - sucesso troca o formulário pela confirmação na região `role="status"`;
 *   - 429 mostra o texto de limite; erro reabilita o botão;
 *   - sem violação de a11y (axe em jsdom, contraste fica no Playwright).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '@/lib/api/client';
import { submitLead } from '@/lib/api/leads';
import { assertNoA11yViolations } from '@/test/a11y';

import { ContactForm } from '../contact-form';
import { contact, manual } from '../content';

vi.mock('@/lib/api/leads', () => ({ submitLead: vi.fn() }));

const submitLeadMock = vi.mocked(submitLead);

function renderForm() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactForm />
    </QueryClientProvider>,
  );
}

async function fillRequired(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(contact.fields.name), 'Maria Contadora');
  await user.type(screen.getByLabelText(contact.fields.email), 'maria@exemplo.com.br');
}

function submitButton() {
  return screen.getByRole('button', { name: contact.submit });
}

beforeEach(() => {
  submitLeadMock.mockReset();
});

describe('ContactForm', () => {
  it('mantém o honeypot fora da árvore acessível e fora do Tab', async () => {
    const { container } = renderForm();
    expect(screen.queryByRole('textbox', { name: 'Site' })).toBeNull();
    const honeypot = container.querySelector<HTMLInputElement>('#lead-website');
    expect(honeypot).not.toBeNull();
    expect(honeypot?.tabIndex).toBe(-1);
    expect(honeypot?.getAttribute('autocomplete')).toBe('off');
    expect(honeypot?.closest('[aria-hidden="true"]')).not.toBeNull();
    await assertNoA11yViolations(container);
  });

  it('sem consentimento não envia e diz por quê', async () => {
    const user = userEvent.setup();
    renderForm();
    await fillRequired(user);
    await user.click(submitButton());
    expect(await screen.findByText(contact.errors.consent)).toBeVisible();
    expect(submitLeadMock).not.toHaveBeenCalled();
  });

  it('campos obrigatórios vazios mostram o erro ligado ao campo', async () => {
    const user = userEvent.setup();
    renderForm();
    await user.click(submitButton());
    const name = screen.getByLabelText(contact.fields.name);
    expect(await screen.findByText(contact.errors.name)).toBeVisible();
    expect(name).toHaveAttribute('aria-invalid', 'true');
    const describedBy = name.getAttribute('aria-describedby') ?? '';
    const message = screen.getByText(contact.errors.name);
    expect(describedBy.split(' ')).toContain(message.id);
    expect(submitLeadMock).not.toHaveBeenCalled();
  });

  it('sucesso envia o payload com o honeypot vazio e troca pela confirmação', async () => {
    submitLeadMock.mockResolvedValue({ received: true });
    const user = userEvent.setup();
    const { container } = renderForm();
    await fillRequired(user);
    await user.type(screen.getByLabelText(/WhatsApp/), '+55 (71) 99999-0000');
    await user.click(screen.getByRole('checkbox'));
    await user.click(submitButton());

    const status = screen.getByRole('status');
    await waitFor(() => expect(status).toHaveTextContent(contact.successTitle));
    // Só o 1º argumento: o TanStack v5 passa um contexto ao `mutationFn` como 2º.
    expect(submitLeadMock.mock.calls[0]?.[0]).toEqual({
      name: 'Maria Contadora',
      email: 'maria@exemplo.com.br',
      company: null,
      whatsapp: '+55 (71) 99999-0000',
      message: null,
      consent: true,
      website: '',
    });
    expect(screen.queryByRole('button', { name: contact.submit })).toBeNull();
    await assertNoA11yViolations(container);
  });

  it('a confirmação oferece o manual para baixar, com o tamanho, na mesma aba', async () => {
    submitLeadMock.mockResolvedValue({ received: true });
    const user = userEvent.setup();
    const { container } = renderForm();
    await fillRequired(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(submitButton());

    const link = await screen.findByRole('link', { name: new RegExp(manual.successLead) });
    expect(link).toHaveAttribute('href', manual.href);
    expect(link).toHaveAttribute('download');
    expect(link).not.toHaveAttribute('target');
    expect(link).toHaveAccessibleDescription(manual.size);
    expect(screen.getByRole('status')).toContainElement(link);
    await assertNoA11yViolations(container);
  });

  it('429 mostra o texto de limite e reabilita o botão', async () => {
    submitLeadMock.mockRejectedValue(
      new ApiError(429, {
        code: 'RATE_LIMITED',
        message: 'Rate limit excedido',
        userMessage: 'Muitas tentativas.',
      }),
    );
    const user = userEvent.setup();
    renderForm();
    await fillRequired(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(submitButton());
    expect(await screen.findByRole('alert')).toHaveTextContent(contact.errors.rateLimited);
    expect(submitButton()).toBeEnabled();
  });

  it('rede fora mostra a mensagem de conexão e reabilita o botão', async () => {
    submitLeadMock.mockRejectedValue(new NetworkError(new TypeError('Failed to fetch')));
    const user = userEvent.setup();
    renderForm();
    await fillRequired(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(submitButton());
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Não foi possível conectar ao servidor',
    );
    expect(submitButton()).toBeEnabled();
  });

  it('erro do servidor mostra o texto genérico', async () => {
    submitLeadMock.mockRejectedValue(
      new ApiError(500, { code: 'INTERNAL_ERROR', message: 'x', userMessage: 'y' }),
    );
    const user = userEvent.setup();
    renderForm();
    await fillRequired(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(submitButton());
    expect(await screen.findByRole('alert')).toHaveTextContent(contact.errors.generic);
    expect(submitButton()).toBeEnabled();
  });

  it('o link do aviso de privacidade abre em nova aba com rel=noopener', () => {
    renderForm();
    const link = screen.getByRole('link', { name: /aviso de privacidade/ });
    expect(link).toHaveAttribute('href', '/privacidade');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener');
  });
});

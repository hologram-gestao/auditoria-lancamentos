/**
 * Trava do defeito 86e2ggm7r: o campo de senha ficava SEM nome acessível.
 *
 * Causa-raiz: `<FormControl>` é um `Slot` do Radix — ele repassa o `id` gerado
 * pelo `FormItem` para o PRIMEIRO filho. Havia uma `<div className="relative">`
 * ali no meio, então a `<label>Senha</label>` apontava para a DIV e o `<input>`
 * ficava com `id=null`. Leitor de tela anunciava "edição", sem rótulo — e o
 * `getByLabel('Senha')` do Playwright resolvia para o BOTÃO do olho, o que
 * derrubava o `beforeEach` de toda a suíte de a11y do DoD.
 *
 * O teste monta o `FormItem`/`FormControl` de verdade (não um mock) porque o
 * defeito era exatamente na COSTURA entre o Slot e o campo.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useForm } from 'react-hook-form';
import { describe, expect, it } from 'vitest';

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { PasswordInput } from '@/components/ui/password-input';
import { assertNoA11yViolations } from '@/test/a11y';

function Harness() {
  const form = useForm<{ password: string }>({ defaultValues: { password: '' } });
  return (
    <Form {...form}>
      <form>
        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Senha</FormLabel>
              <FormControl>
                <PasswordInput autoComplete="current-password" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </form>
    </Form>
  );
}

describe('PasswordInput dentro de FormControl', () => {
  it('a label aponta para o INPUT (nome acessível), não para o wrapper', () => {
    render(<Harness />);
    const field = screen.getByLabelText('Senha', { selector: 'input' });
    expect(field).toHaveAttribute('type', 'password');
    expect(field.id).not.toBe('');
  });

  it('o botão de olho tem nome próprio e alterna o tipo do input', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const field = screen.getByLabelText('Senha', { selector: 'input' });

    await user.click(screen.getByRole('button', { name: 'Mostrar senha' }));
    expect(field).toHaveAttribute('type', 'text');

    await user.click(screen.getByRole('button', { name: 'Ocultar senha' }));
    expect(field).toHaveAttribute('type', 'password');
  });

  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(<Harness />);
    await assertNoA11yViolations(container);
  });
});

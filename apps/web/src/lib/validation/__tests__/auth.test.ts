/**
 * `loginSchema` (86e2n39eg): o e-mail passa por `trim` antes de validar e de
 * enviar. O teste é no SCHEMA e não na tela de propósito: num `<input
 * type="email">` o próprio browser (e o jsdom) já remove espaço das pontas
 * do valor, então pela tela o `trim` nunca aparece. Ele existe para o caso
 * em que o valor chega sem essa sanitização (outro tipo de campo, teste,
 * preenchimento programático).
 */
import { describe, expect, it } from 'vitest';

import { loginSchema } from '@/lib/validation/auth';

describe('loginSchema', () => {
  it('tira o espaço das pontas do e-mail antes de validar e de enviar', () => {
    const parsed = loginSchema.parse({ email: '  ana@hologram.com.br \t', password: 'x' });
    expect(parsed.email).toBe('ana@hologram.com.br');
  });

  it('só espaço conta como e-mail vazio', () => {
    const result = loginSchema.safeParse({ email: '   ', password: 'x' });
    expect(result.success).toBe(false);
    expect(result.error?.issues[0]?.message).toBe('Informe seu e-mail.');
  });

  it('formato inválido continua "E-mail inválido."', () => {
    const result = loginSchema.safeParse({ email: 'joao@', password: 'x' });
    expect(result.error?.issues[0]?.message).toBe('E-mail inválido.');
  });

  it('a senha NÃO passa por trim (espaço pode ser parte dela)', () => {
    expect(loginSchema.parse({ email: 'a@b.com', password: ' s ' }).password).toBe(' s ');
  });
});

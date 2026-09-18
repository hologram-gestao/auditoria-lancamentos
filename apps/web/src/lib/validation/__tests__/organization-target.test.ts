/**
 * "Onde o recurso NASCE" no formulário — a regra compartilhada pelos três
 * formulários de criação (86e36ed1d).
 *
 * O backend tem UMA decisão (`resolve_organization_for_creation`): a plataforma
 * escolhe e é obrigada a escolher; o staff não escolhe — a organização vem da
 * LINHA dele. Aqui prova-se que os três formulários espelham essa decisão do
 * mesmo jeito, porque três cópias divergindo é exatamente o defeito que a
 * fábrica única existe para impedir.
 *
 * O teste vale contra o que o usuário VÊ (a mensagem em português) e contra o
 * que o servidor RECEBE (campo ausente para o staff).
 */
import { describe, expect, it } from 'vitest';

import { makeClientCategorySchema } from '@/lib/validation/client-categories';
import { makeCreateClientSchema } from '@/lib/validation/clients';
import { makeCreateUserSchema } from '@/lib/validation/users';

const ORG = '0706eeb5-9718-4d03-bcda-ef615789e6ac';

const CLIENT_BASE = {
  name: 'Prospecta Contabilidade',
  omie_app_key: 'key-123',
  omie_app_secret: 'secret-123',
  category_id: 'none',
};
const USER_BASE = {
  name: 'Fulano de Tal',
  email: 'fulano@hologram.com.br',
  password: 'senha-de-teste',
  role: 'manager' as const,
};
const CATEGORY_BASE = { name: 'Varejo', tone: 'neutral' as const };

const CASES = [
  { label: 'cliente', make: makeCreateClientSchema, base: CLIENT_BASE },
  { label: 'usuário', make: makeCreateUserSchema, base: USER_BASE },
  { label: 'categoria', make: makeClientCategorySchema, base: CATEGORY_BASE },
];

describe.each(CASES)('organização de destino — formulário de $label', ({ make, base }) => {
  it('PLATAFORMA: sem escolher, recusa com a mensagem que o usuário lê', () => {
    const result = make({ requireOrganization: true }).safeParse({ ...base, organization_id: '' });

    expect(result.success).toBe(false);
    if (result.success) return;
    const issue = result.error.issues.find((i) => i.path[0] === 'organization_id');
    expect(issue?.message).toBe('Escolha a organização de destino.');
  });

  it('PLATAFORMA: com a organização escolhida, passa', () => {
    const result = make({ requireOrganization: true }).safeParse({
      ...base,
      organization_id: ORG,
    });

    expect(result.success).toBe(true);
  });

  it('STAFF: o campo nem existe no formulário, e a ausência dele passa', () => {
    // É a ausência que faz o backend usar a organização da LINHA do ator.
    // Mandar uma organização divergente seria 403, nunca ignorado.
    const result = make().safeParse(base);

    expect(result.success).toBe(true);
    if (!result.success) return;
    expect(result.data.organization_id).toBeUndefined();
  });
});

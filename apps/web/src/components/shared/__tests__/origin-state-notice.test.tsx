/**
 * Os TRÊS códigos da taxonomia de origem viram ESTADO, nunca toast genérico
 * (Sprint 9 / R3 · R7).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 *
 * O que este arquivo trava:
 *   - cada `ApiError` com um dos três códigos produz copy PRÓPRIA — trocar uma
 *     pela outra é o defeito que o PRD nomeia (mandar reconectar quem nunca
 *     conectou, ou mandar conectar quem já tem conexão);
 *   - erro que NÃO é da taxonomia devolve `null`, e é isso que deixa o caller
 *     degradar como sempre degradou (toast/`ErrorState`);
 *   - `CAPACIDADE_AUSENTE` não oferece ação: não há o que consertar;
 *   - sem `manage_client_connections` o usuário vê o estado e NÃO a ação (R5).
 *
 * ⚠️ Os códigos são MAIÚSCULOS. O PRD escreve `sem_conexao`, mas o que viaja no
 * corpo é `ErrorCode.SEM_CONEXAO`. Este arquivo existe também para travar isso.
 */
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/clientes/c1',
  useSearchParams: () => new URLSearchParams(''),
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

import { OriginStateNotice } from '@/components/shared/origin-state-notice';
import { ApiError } from '@/lib/api/client';
import type { AuthenticatedUser } from '@/lib/contracts';
import { isOriginError, originErrorCode } from '@/lib/origin-state';
import { assertNoA11yViolations } from '@/test/a11y';

const CLIENT_ID = 'c1';

function staff(over: Partial<AuthenticatedUser> = {}): AuthenticatedUser {
  return {
    id: 'me',
    email: 'gerente@hologram.com.br',
    name: 'Gerente',
    role: 'manager',
    scope: 'system',
    client_id: null,
    organization_id: 'org-1',
    organization_name: 'Hologram',
    ...over,
  };
}

function apiError(code: string, status = 409): ApiError {
  return new ApiError(status, {
    code,
    message: 'internal',
    userMessage: 'Mensagem crua do servidor.',
  });
}

beforeEach(() => {
  authState.user = staff();
});

describe('originErrorCode — só os três da taxonomia', () => {
  it('reconhece os três códigos, em MAIÚSCULAS', () => {
    expect(originErrorCode(apiError('SEM_CONEXAO'))).toBe('SEM_CONEXAO');
    expect(originErrorCode(apiError('ORIGEM_COM_ERRO'))).toBe('ORIGEM_COM_ERRO');
    expect(originErrorCode(apiError('CAPACIDADE_AUSENTE'))).toBe('CAPACIDADE_AUSENTE');
  });

  it('não confunde o nome do PRD (minúsculo) com o código do contrato', () => {
    // Se um dia alguém "corrigir" o backend para minúsculas, é aqui que aparece.
    expect(originErrorCode(apiError('sem_conexao'))).toBeNull();
  });

  it('erro que não é da taxonomia devolve null (o caller degrada como sempre)', () => {
    expect(originErrorCode(apiError('CONFLICT'))).toBeNull();
    expect(originErrorCode(new Error('boom'))).toBeNull();
    expect(originErrorCode(null)).toBeNull();
    expect(isOriginError(new Error('boom'))).toBe(false);
    expect(isOriginError(apiError('SEM_CONEXAO'))).toBe(true);
  });
});

describe('OriginStateNotice — copy distinta por código', () => {
  it('SEM_CONEXAO manda CONECTAR', () => {
    render(<OriginStateNotice error={apiError('SEM_CONEXAO')} clientId={CLIENT_ID} />);
    expect(screen.getByText('Este cliente não tem origem conectada')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Conectar origem' })).toHaveAttribute(
      'href',
      `/clientes/${CLIENT_ID}/painel`,
    );
  });

  it('ORIGEM_COM_ERRO manda RECONECTAR — e nunca diz "sem origem"', () => {
    render(<OriginStateNotice error={apiError('ORIGEM_COM_ERRO')} clientId={CLIENT_ID} />);
    expect(screen.getByText('A origem deste cliente está com erro')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Reconectar origem' })).toBeVisible();
    expect(screen.queryByText('Este cliente não tem origem conectada')).toBeNull();
  });

  it('CAPACIDADE_AUSENTE não oferece ação: não há o que consertar', () => {
    render(<OriginStateNotice error={apiError('CAPACIDADE_AUSENTE')} clientId={CLIENT_ID} />);
    expect(screen.getByText('A origem conectada não faz esta operação')).toBeVisible();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('erro fora da taxonomia não renderiza nada (devolve null)', () => {
    const { container } = render(
      <OriginStateNotice error={apiError('INTERNAL_ERROR', 500)} clientId={CLIENT_ID} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe('OriginStateNotice — a ação respeita a permissão do R5', () => {
  it('operador do cliente vê o estado e NÃO a ação', () => {
    authState.user = staff({
      role: 'client_operator',
      scope: 'client',
      client_id: CLIENT_ID,
    });
    render(<OriginStateNotice error={apiError('SEM_CONEXAO')} clientId={CLIENT_ID} />);
    expect(screen.getByText('Este cliente não tem origem conectada')).toBeVisible();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('dentro de gaveta (`showAction=false`) fica só a explicação', () => {
    render(
      <OriginStateNotice error={apiError('SEM_CONEXAO')} clientId={CLIENT_ID} showAction={false} />,
    );
    expect(screen.getByText('Este cliente não tem origem conectada')).toBeVisible();
    expect(screen.queryByRole('link')).toBeNull();
  });
});

describe('OriginStateNotice — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(
      <OriginStateNotice error={apiError('ORIGEM_COM_ERRO')} clientId={CLIENT_ID} />,
    );
    await assertNoA11yViolations(container);
  });
});

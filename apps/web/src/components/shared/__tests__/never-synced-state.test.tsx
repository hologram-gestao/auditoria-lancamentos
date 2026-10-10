/**
 * O vazio "ainda não sincronizou" (86e3n70qj): a frase de apoio é UMA decisão,
 * na ordem encerrado → permissão → origem → o que a sincronização traz. A ação
 * só aparece quando quem chama a passou (já decidida pela permissão).
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  NEVER_SYNCED_CLOSED,
  NEVER_SYNCED_NO_PERMISSION,
  NEVER_SYNCED_ORIGIN_BLOCKED,
  NeverSyncedState,
  neverSyncedDescription,
} from '@/components/shared/never-synced-state';
import { assertNoA11yViolations } from '@/test/a11y';

const PURPOSE = 'Sincronizar agora traz os títulos.';

describe('neverSyncedDescription', () => {
  it('encerrado vence tudo', () => {
    expect(
      neverSyncedDescription({ purpose: PURPOSE, canSync: true, isClosed: true, hasAction: false }),
    ).toBe(NEVER_SYNCED_CLOSED);
  });

  it('sem permissão: "peça a alguém com acesso"', () => {
    expect(
      neverSyncedDescription({
        purpose: PURPOSE,
        canSync: false,
        isClosed: false,
        hasAction: false,
      }),
    ).toBe(NEVER_SYNCED_NO_PERMISSION);
  });

  it('com permissão e sem botão: a origem é o motivo', () => {
    expect(
      neverSyncedDescription({
        purpose: PURPOSE,
        canSync: true,
        isClosed: false,
        hasAction: false,
      }),
    ).toBe(NEVER_SYNCED_ORIGIN_BLOCKED);
  });

  it('com permissão e botão: o que a sincronização traz', () => {
    expect(
      neverSyncedDescription({ purpose: PURPOSE, canSync: true, isClosed: false, hasAction: true }),
    ).toBe(PURPOSE);
  });
});

describe('NeverSyncedState', () => {
  it('mostra o título, a frase e a ação dentro do vazio', async () => {
    const { container } = render(
      <NeverSyncedState
        title="Esta carteira ainda não foi sincronizada com o Omie"
        purpose={PURPOSE}
        canSync
        isClosed={false}
        action={<button type="button">Sincronizar agora</button>}
        vignette={<svg aria-hidden="true" />}
      />,
    );
    expect(screen.getByText('Esta carteira ainda não foi sincronizada com o Omie')).toBeVisible();
    expect(screen.getByText(PURPOSE)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Sincronizar agora' })).toBeVisible();
    await assertNoA11yViolations(container);
  });

  it('sem ação, nenhum botão aparece', () => {
    render(
      <NeverSyncedState
        title="Título"
        purpose={PURPOSE}
        canSync={false}
        isClosed={false}
        action={null}
        vignette={<svg aria-hidden="true" />}
      />,
    );
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText(NEVER_SYNCED_NO_PERMISSION)).toBeVisible();
  });
});

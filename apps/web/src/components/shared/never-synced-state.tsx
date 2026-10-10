/**
 * "Ainda não sincronizou" dentro do vazio de uma tela sincronizável (86e3n70qj).
 *
 * Origem: na reunião de 08/10/2026 a carteira abriu vazia até alguém clicar em
 * "Sincronizar", e o vazio não dizia isso. A regra que fica, para toda tela que
 * só tem dado depois de ir à origem (carteira, categorias do Omie):
 *
 *   - o vazio DIZ que a tela ainda não foi sincronizada (título de quem chama);
 *   - quem tem a permissão recebe "Sincronizar agora" DENTRO do vazio (`action`,
 *     um slot já decidido pela permissão, pela origem e pelo encerramento);
 *   - quem não tem lê "peça a alguém com acesso", e quem tem mas está barrado
 *     pela origem lê para olhar o aviso da origem (que a tela mostra acima).
 *
 * Sincronizar sozinho na primeira abertura NÃO entra: gastaria chamada à origem
 * sem ninguém pedir, e o botão no vazio basta (decisão registrada na 86e3n70qj).
 *
 * As quatro frases moram aqui, numa decisão só (`neverSyncedDescription`): as
 * telas passam o que muda entre elas (o título, o que a sincronização traz e a
 * vinheta). Server component: a ação, quando há, vem pronta de quem chama.
 */
import { EmptyState } from '@/components/shared/empty-state';

export interface NeverSyncedStateProps {
  /** "Esta carteira ainda não foi sincronizada com o Omie". */
  title: string;
  /** O que a sincronização traz, para quem pode sincronizar agora. */
  purpose: string;
  canSync: boolean;
  isClosed: boolean;
  /** O botão "Sincronizar agora", ou `null` quando a tela não o oferece. */
  action: React.ReactNode;
  vignette: React.ReactNode;
}

export const NEVER_SYNCED_CLOSED =
  'O cliente foi encerrado antes de sincronizar, e a sincronização não fica disponível para clientes encerrados.';
export const NEVER_SYNCED_NO_PERMISSION =
  'Peça a alguém com acesso de sincronização para sincronizar. Depois disso, os dados aparecem aqui.';
export const NEVER_SYNCED_ORIGIN_BLOCKED =
  'A sincronização depende de uma origem conectada e ativa: veja o aviso da origem nesta tela.';

/** A frase de apoio do vazio. Uma decisão só, na ordem: encerrado, permissão, origem. */
export function neverSyncedDescription({
  purpose,
  canSync,
  isClosed,
  hasAction,
}: {
  purpose: string;
  canSync: boolean;
  isClosed: boolean;
  hasAction: boolean;
}): string {
  if (isClosed) return NEVER_SYNCED_CLOSED;
  if (!canSync) return NEVER_SYNCED_NO_PERMISSION;
  if (!hasAction) return NEVER_SYNCED_ORIGIN_BLOCKED;
  return purpose;
}

export function NeverSyncedState({
  title,
  purpose,
  canSync,
  isClosed,
  action,
  vignette,
}: NeverSyncedStateProps) {
  const hasAction = action !== null && action !== undefined && action !== false;
  return (
    <EmptyState
      framed={false}
      vignette={vignette}
      title={title}
      description={neverSyncedDescription({ purpose, canSync, isClosed, hasAction })}
      action={hasAction ? action : undefined}
    />
  );
}

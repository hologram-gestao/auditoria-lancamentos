/**
 * `/clientes/{clientId}/painel` — endereço ANTIGO do painel (Sprint 4 / R7 até a
 * 86e3k1q5n). O painel virou a raiz do cliente; este endereço redireciona de
 * forma permanente para link salvo e favorito não quebrarem. A query vai junto:
 * o `?conectar=<tipo>` (abre a gaveta de conexão, 86e3fqnc9) nasceu apontando
 * para cá.
 */

import { permanentRedirect } from 'next/navigation';

export default function LegacyClientDashboardPage({
  params,
  searchParams,
}: {
  params: { clientId: string };
  searchParams: Record<string, string | string[] | undefined>;
}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(searchParams)) {
    const values = Array.isArray(value) ? value : value === undefined ? [] : [value];
    for (const item of values) query.append(key, item);
  }
  const suffix = query.toString() === '' ? '' : `?${query.toString()}`;
  permanentRedirect(`/clientes/${encodeURIComponent(params.clientId)}${suffix}`);
}

/**
 * Server wrapper da tela de Organizações — 86e36ecwa.
 *
 * O guard de RBAC fica no client component (mesmo desenho das outras telas de
 * configuração): o middleware Next só checa autenticação e não é barreira
 * (CVE-2025-29927). O backend nega qualquer acesso não-plataforma com 403 nas
 * quatro rotas de `/api/v1/organizations` — é ele a autoridade.
 */

import OrganizationsPage from './organizations-page';

export default function Page() {
  return <OrganizationsPage />;
}

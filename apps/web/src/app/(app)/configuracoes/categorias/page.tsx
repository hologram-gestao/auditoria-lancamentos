/**
 * Server wrapper da tela admin de Categorias de Cliente — 86e34jd8m.
 *
 * O guard de RBAC fica no client component (mesmo desenho dos Tipos de
 * Anomalia): o middleware Next só checa autenticação. O backend nega
 * qualquer escrita de não-admin com 403 — é ele a autoridade.
 */

import ClientCategoriesPage from './client-categories-page';

export default function Page() {
  return <ClientCategoriesPage />;
}

/**
 * Server wrapper da tela admin de Tipos de Anomalia — S15 FRONT 11.2.
 *
 * O guard de RBAC fica no client component (defesa em profundidade — JWT
 * só é decodificado no client porque o cookie é HttpOnly e o middleware
 * Next só checa autenticação, não role). Backend retorna 403 em qualquer
 * mutação caso um non-admin tente.
 */

import AnomalyTypesPage from './anomaly-types-page';

export default function Page() {
  return <AnomalyTypesPage />;
}

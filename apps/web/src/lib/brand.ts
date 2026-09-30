/**
 * Marca do produto num lugar só (subtask 86e3fr9x3).
 *
 * O nome novo do produto ainda não foi decidido (86e3fr9wm). Até lá os valores são
 * os de hoje, SEM mudança visual; quando o nome existir, a troca é aqui (e em
 * `apps/api/app/core/branding.py`, o espelho do backend). Um teste
 * (`src/lib/__tests__/brand.test.ts`) recusa o nome antigo escrito à mão fora
 * deste arquivo.
 *
 * A landing pública NÃO usa o nome do produto: fala em "a plataforma da
 * Hologram" (decisão D2 do plano da landing), então ela lê só `COMPANY_*`.
 */

/** Razão social curta, como aparece no rodapé e nos textos institucionais. */
export const COMPANY_NAME = 'Hologram Gestão';

/** Nome curto da empresa, como aparece ao lado da logomark. */
export const COMPANY_SHORT_NAME = 'Hologram';

/** Nome do produto dentro do app autenticado. */
export const PRODUCT_NAME = 'Auditoria de Lançamentos';

/** Sigla usada em textos operacionais ("criado pelo ADL"). */
export const PRODUCT_SHORT_NAME = 'ADL';

/** Título completo: aba do navegador e tela de login. */
export const PRODUCT_TITLE = `Sistema de ${PRODUCT_NAME}`;

/** Descrição dos metadados do app autenticado. */
export const PRODUCT_TAGLINE = `Plataforma interna da ${COMPANY_NAME} para conciliação bancária.`;

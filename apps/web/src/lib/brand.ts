/**
 * Marca do produto num lugar só (subtask 86e3fr9x3).
 *
 * O produto se chama **Hologram OS** (decisão do Pedro, 30/09/2026); a empresa
 * continua Hologram Gestão, que é a primeira organização da plataforma. A troca
 * de nome é aqui e em `apps/api/app/core/branding.py`, o espelho do backend. Um
 * teste (`src/lib/__tests__/brand.test.ts`) recusa o nome ANTIGO escrito em
 * qualquer arquivo e o nome novo escrito à mão fora deste.
 *
 * A landing pública nomeia o produto (a D2 do plano caiu em 30/09): ela lê
 * `PRODUCT_NAME` para o produto e `COMPANY_*` para quem trata o dado.
 */

/** Razão social curta, como aparece no rodapé e nos textos institucionais. */
export const COMPANY_NAME = 'Hologram Gestão';

/** Nome curto da empresa, como aparece ao lado da logomark. */
export const COMPANY_SHORT_NAME = 'Hologram';

/** Nome do produto: header do app, landing, textos de tela. */
export const PRODUCT_NAME = 'Hologram OS';

/**
 * Nome usado em textos operacionais ("criado pelo Hologram OS"). Sem sigla: o
 * nome já é curto, e uma sigla nova seria um terceiro nome para o mesmo produto.
 */
export const PRODUCT_SHORT_NAME = PRODUCT_NAME;

/** Título completo: aba do navegador e tela de login. */
export const PRODUCT_TITLE = PRODUCT_NAME;

/** Descrição dos metadados do app autenticado. */
export const PRODUCT_TAGLINE = `Plataforma da ${COMPANY_NAME} para conciliar, classificar e fechar o financeiro de cada cliente.`;

/**
 * Domínio próprio do produto, ainda NÃO registrado (86e3fr9wm). Não é usado para
 * montar URL: a URL absoluta da landing vem de `NEXT_PUBLIC_SITE_URL`, que só existe
 * quando o domínio apontar (`Docs/landing/DOMINIO_E_NOME.md`).
 */
export const PRODUCT_DOMAIN = 'hologramos.com.br';

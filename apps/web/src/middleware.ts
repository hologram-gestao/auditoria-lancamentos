/**
 * Next middleware — proteção de rotas baseada APENAS na presença do cookie HttpOnly.
 *
 * Por que só presença, e não validação de JWT aqui?
 *   - Validação real (assinatura + active=true no DB) é responsabilidade do backend
 *     (Doc §7 + CLAUDE.md §3.12). Re-validar no edge custa CPU e pode causar drift
 *     entre clock do edge e do backend.
 *   - O middleware só decide se vale a pena navegar — qualquer fetch real para
 *     o backend faz a validação completa e dispara refresh interceptor se preciso.
 *
 * Comportamento:
 *   - Rotas PÚBLICAS de marca (`PUBLIC_PATHS`: a landing `/` e o aviso de
 *     privacidade, épico 86e3fr9tj) passam sem cookie. Com cookie, `/` vai para
 *     `/clientes` (quem já entrou não precisa da página de apresentação) e
 *     `/privacidade` continua passando (é leitura de qualquer um).
 *   - `/login` com cookie presente → redireciona para `/clientes`.
 *   - Qualquer outra rota sem cookie → redireciona para `/login`.
 *   - Assets, API routes do Next e o proxy `/api/*` são excluídos pelo `matcher`.
 */
import { type NextRequest, NextResponse } from 'next/server';

const ACCESS_COOKIE = 'access_token';
const LOGIN_PATH = '/login';
const HOME_PATH = '/clientes';
const LANDING_PATH = '/';

/** Rotas que qualquer visitante abre, logado ou não. */
const PUBLIC_PATHS: readonly string[] = [LANDING_PATH, '/privacidade'];

function redirectTo(request: NextRequest, pathname: string): NextResponse {
  const url = request.nextUrl.clone();
  url.pathname = pathname;
  return NextResponse.redirect(url);
}

export function middleware(request: NextRequest) {
  const hasAccessCookie = Boolean(request.cookies.get(ACCESS_COOKIE)?.value);
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.includes(pathname)) {
    if (pathname === LANDING_PATH && hasAccessCookie) {
      return redirectTo(request, HOME_PATH);
    }
    return NextResponse.next();
  }

  if (pathname === LOGIN_PATH) {
    return hasAccessCookie ? redirectTo(request, HOME_PATH) : NextResponse.next();
  }

  if (!hasAccessCookie) {
    return redirectTo(request, LOGIN_PATH);
  }

  return NextResponse.next();
}

export const config = {
  // Roda em todas as rotas EXCETO assets, _next, favicon, arquivos estáticos
  // e o proxy `/api/*` (chamadas pro backend que o Next reverse-proxia via
  // rewrites). Sem excluir `api`, o middleware redirecionaria POST de login
  // pra /login (307), e o browser repetia o POST em /login → 405. O backend
  // valida o cookie real; aqui basta evitar o falso positivo. `robots.txt` já
  // fica de fora pela regra do ponto (`.*\\..*`).
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)'],
};

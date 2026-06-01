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
 *   - `/login` com cookie presente → redireciona para `/clientes`.
 *   - Qualquer outra rota protegida sem cookie → redireciona para `/login`.
 *   - Rotas públicas (assets, API routes do Next) são excluídas pelo `matcher`.
 */
import { type NextRequest, NextResponse } from 'next/server';

const ACCESS_COOKIE = 'access_token';
const LOGIN_PATH = '/login';
const HOME_PATH = '/clientes';

export function middleware(request: NextRequest) {
  const hasAccessCookie = Boolean(request.cookies.get(ACCESS_COOKIE)?.value);
  const { pathname } = request.nextUrl;

  if (pathname === LOGIN_PATH) {
    if (hasAccessCookie) {
      const url = request.nextUrl.clone();
      url.pathname = HOME_PATH;
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }

  if (!hasAccessCookie) {
    const url = request.nextUrl.clone();
    url.pathname = LOGIN_PATH;
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Roda em todas as rotas EXCETO assets, _next, favicon, arquivos estáticos
  // e o proxy `/api/*` (chamadas pro backend que o Next reverse-proxia via
  // rewrites). Sem excluir `api`, o middleware redirecionaria POST de login
  // pra /login (307), e o browser repetia o POST em /login → 405. O backend
  // valida o cookie real; aqui basta evitar o falso positivo.
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)'],
};

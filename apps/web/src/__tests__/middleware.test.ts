// @vitest-environment node
/**
 * Middleware de rotas (86e3fr9vz): a landing e o aviso de privacidade são públicos;
 * o resto segue exigindo o cookie de sessão.
 */
import { NextRequest } from 'next/server';
import { describe, expect, it } from 'vitest';

import { middleware } from '../middleware';

const ORIGIN = 'https://adl.exemplo';

function request(pathname: string, { withCookie }: { withCookie: boolean }): NextRequest {
  const headers = new Headers();
  if (withCookie) headers.set('cookie', 'access_token=qualquer');
  return new NextRequest(new URL(pathname, ORIGIN), { headers });
}

function redirectPath(response: Response): string | null {
  const location = response.headers.get('location');
  return location ? new URL(location).pathname : null;
}

/** `NextResponse.next()` sinaliza "segue" com este header, sem `location`. */
function passesThrough(response: Response): boolean {
  return response.headers.get('x-middleware-next') === '1' && !response.headers.get('location');
}

describe('middleware', () => {
  it('/ sem cookie passa (landing pública)', () => {
    expect(passesThrough(middleware(request('/', { withCookie: false })))).toBe(true);
  });

  it('/ com cookie vai para /clientes', () => {
    const res = middleware(request('/', { withCookie: true }));
    expect(res.status).toBe(307);
    expect(redirectPath(res)).toBe('/clientes');
  });

  it('/privacidade passa com e sem cookie', () => {
    expect(passesThrough(middleware(request('/privacidade', { withCookie: false })))).toBe(true);
    expect(passesThrough(middleware(request('/privacidade', { withCookie: true })))).toBe(true);
  });

  it('/login com cookie vai para /clientes', () => {
    expect(redirectPath(middleware(request('/login', { withCookie: true })))).toBe('/clientes');
  });

  it('/login sem cookie passa', () => {
    expect(passesThrough(middleware(request('/login', { withCookie: false })))).toBe(true);
  });

  it('/clientes sem cookie vai para /login', () => {
    expect(redirectPath(middleware(request('/clientes', { withCookie: false })))).toBe('/login');
  });

  it('/clientes com cookie passa', () => {
    expect(passesThrough(middleware(request('/clientes', { withCookie: true })))).toBe(true);
  });

  it('uma rota parecida com a pública não é pública', () => {
    expect(redirectPath(middleware(request('/privacidade-x', { withCookie: false })))).toBe(
      '/login',
    );
  });
});

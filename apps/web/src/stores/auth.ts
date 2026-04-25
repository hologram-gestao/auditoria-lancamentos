/**
 * Store em memória do usuário autenticado.
 *
 * Apenas dados públicos (id, email, name, role). Tokens vivem em cookies HttpOnly
 * e NUNCA são lidos por JS (CLAUDE.md §3 + §6 do brief de S3).
 *
 * Sem persistência em localStorage — após F5 a verdade vem do servidor (cookie + /me).
 */
import { create } from 'zustand';

import type { AuthenticatedUser } from '@/lib/api/auth';

interface AuthState {
  user: AuthenticatedUser | null;
  isAuthenticated: boolean;
  setUser: (user: AuthenticatedUser) => void;
  clearUser: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  setUser: (user) => set({ user, isAuthenticated: true }),
  clearUser: () => set({ user: null, isAuthenticated: false }),
}));

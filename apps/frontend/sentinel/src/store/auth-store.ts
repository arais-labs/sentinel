import { create } from 'zustand';

import { AUTH_BASE_URL } from '../lib/env';
import { decodeJwt, tokenExpiresSoon } from '../lib/jwt';
import type { AuthStatus, TokenResponse } from '../types/api';

interface AuthSnapshot {
  accessToken: string;
  tokenType: string;
  expiresIn: number;
}

interface AuthState extends AuthSnapshot {
  status: AuthStatus;
  userId: string | null;
  role: string | null;
  errorMessage: string | null;
  initialize: () => void;
  login: (username: string, password: string) => Promise<boolean>;
  refresh: () => Promise<boolean>;
  logout: () => Promise<void>;
  clearSession: () => void;
  getValidAccessToken: () => Promise<string | null>;
}

function emptySession() {
  return {
    accessToken: '',
    tokenType: 'bearer',
    expiresIn: 0,
  };
}

function parseAuthError(payload: unknown, fallback: string) {
  if (typeof payload !== 'object' || payload === null) {
    return fallback;
  }

  const value = payload as { error?: { message?: string }; detail?: string };
  if (value.error?.message) {
    return value.error.message;
  }
  if (value.detail) {
    return value.detail;
  }
  return fallback;
}

interface MeResponse {
  sub: string;
  role: string;
}

async function authRequest(path: string, body?: Record<string, unknown>) {
  const response = await fetch(`${AUTH_BASE_URL}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  const payload = (await response.json().catch(() => ({}))) as unknown;

  if (!response.ok) {
    throw new Error(parseAuthError(payload, 'Authentication failed'));
  }

  return payload as TokenResponse;
}

async function fetchMe(): Promise<MeResponse | null> {
  const response = await fetch(`${AUTH_BASE_URL}/me`, {
    method: 'GET',
    credentials: 'include',
  });
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as MeResponse;
}

function applySession(setter: (partial: Partial<AuthState>) => void, session: TokenResponse) {
  const claims = decodeJwt(session.access_token);
  const snapshot: AuthSnapshot = {
    accessToken: session.access_token,
    tokenType: session.token_type ?? 'bearer',
    expiresIn: session.expires_in ?? 0,
  };

  setter({
    ...snapshot,
    status: 'authenticated',
    userId: claims.sub ?? 'unknown',
    role: claims.role ?? 'agent',
    errorMessage: null,
  });
}

export const useAuthStore = create<AuthState>((set, get) => ({
  ...emptySession(),
  status: 'loading',
  userId: null,
  role: null,
  errorMessage: null,

  initialize: () => {
    void (async () => {
      const me = await fetchMe();
      if (me) {
        set({
          ...emptySession(),
          status: 'authenticated',
          userId: me.sub,
          role: me.role,
          errorMessage: null,
        });
        return;
      }
      const refreshed = await get().refresh();
      if (refreshed) {
        const meAfterRefresh = await fetchMe();
        if (meAfterRefresh) {
          set({
            status: 'authenticated',
            userId: meAfterRefresh.sub,
            role: meAfterRefresh.role,
            errorMessage: null,
          });
          return;
        }
      }
      set({
        ...emptySession(),
        status: 'unauthenticated',
        userId: null,
        role: null,
        errorMessage: null,
      });
    })();
  },

  login: async (username: string, password: string) => {
    set({ status: 'loading', errorMessage: null });

    try {
      const session = await authRequest('/login', {
        username: username.trim(),
        password: password.trim(),
      });
      applySession(set, session);
      return true;
    } catch (error) {
      set({
        ...emptySession(),
        status: 'unauthenticated',
        userId: null,
        role: null,
        errorMessage: error instanceof Error ? error.message : 'Login failed',
      });
      return false;
    }
  },

  refresh: async () => {
    try {
      // Refresh token is stored in an HttpOnly cookie; do not keep it in runtime state.
      const session = await authRequest('/refresh');
      applySession(set, session);
      return true;
    } catch {
      get().clearSession();
      return false;
    }
  },

  logout: async () => {
    try {
      await fetch(`${AUTH_BASE_URL}/session`, {
        method: 'DELETE',
        credentials: 'include',
      });
    } catch {
      // Ignore network failures during logout cleanup.
    }

    get().clearSession();
  },

  clearSession: () => {
    set({
      ...emptySession(),
      status: 'unauthenticated',
      userId: null,
      role: null,
      errorMessage: null,
    });
  },

  getValidAccessToken: async () => {
    const token = get().accessToken;
    if (!token) {
      return null;
    }
    if (!tokenExpiresSoon(token, 30)) {
      return token;
    }

    const refreshed = await get().refresh();
    if (!refreshed) {
      return null;
    }

    return get().accessToken || null;
  },
}));

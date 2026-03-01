import { create } from 'zustand';

import { AUTH_BASE_URL } from '../lib/env';
import { decodeJwt, tokenExpiresSoon } from '../lib/jwt';
import type { AuthStatus, TokenResponse } from '../types/api';

const storageKey = 'sentinel.araios.auth.session';

interface AuthSnapshot {
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  expiresIn: number;
}

interface AuthState extends AuthSnapshot {
  status: AuthStatus;
  userId: string | null;
  role: string | null;
  errorMessage: string | null;
  initialize: () => void;
  login: (apiKey: string) => Promise<boolean>;
  refresh: () => Promise<boolean>;
  logout: () => Promise<void>;
  clearSession: () => void;
  getValidAccessToken: () => Promise<string | null>;
}

function emptySession() {
  return {
    accessToken: '',
    refreshToken: '',
    tokenType: 'bearer',
    expiresIn: 0,
  };
}

function persist(snapshot: AuthSnapshot) {
  localStorage.setItem(storageKey, JSON.stringify(snapshot));
}

function readPersisted(): AuthSnapshot | null {
  const raw = localStorage.getItem(storageKey);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<AuthSnapshot>;
    if (!parsed.accessToken || !parsed.refreshToken) {
      return null;
    }
    return {
      accessToken: parsed.accessToken,
      refreshToken: parsed.refreshToken,
      tokenType: parsed.tokenType ?? 'bearer',
      expiresIn: parsed.expiresIn ?? 0,
    };
  } catch {
    return null;
  }
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

async function authRequest(path: string, body: Record<string, unknown>) {
  const response = await fetch(`${AUTH_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  const payload = (await response.json().catch(() => ({}))) as unknown;

  if (!response.ok) {
    throw new Error(parseAuthError(payload, 'Authentication failed'));
  }

  return payload as TokenResponse;
}

function applySession(setter: (partial: Partial<AuthState>) => void, session: TokenResponse) {
  const claims = decodeJwt(session.access_token);
  const snapshot: AuthSnapshot = {
    accessToken: session.access_token,
    refreshToken: session.refresh_token,
    tokenType: session.token_type ?? 'bearer',
    expiresIn: session.expires_in ?? 0,
  };

  persist(snapshot);
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
    const persisted = readPersisted();
    if (!persisted) {
      set({
        ...emptySession(),
        status: 'unauthenticated',
        userId: null,
        role: null,
      });
      return;
    }

    const claims = decodeJwt(persisted.accessToken);
    set({
      ...persisted,
      status: 'authenticated',
      userId: claims.sub ?? 'unknown',
      role: claims.role ?? 'agent',
      errorMessage: null,
    });
  },

  login: async (apiKey: string) => {
    set({ status: 'loading', errorMessage: null });

    try {
      const session = await authRequest('/token', { api_key: apiKey.trim() });
      applySession(set, session);
      return true;
    } catch (error) {
      localStorage.removeItem(storageKey);
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
    const refreshToken = get().refreshToken;
    if (!refreshToken) {
      return false;
    }

    try {
      const session = await authRequest('/refresh', { refresh_token: refreshToken });
      applySession(set, session);
      return true;
    } catch {
      get().clearSession();
      return false;
    }
  },

  logout: async () => {
    const accessToken = get().accessToken;
    if (accessToken) {
      try {
        await fetch(`${AUTH_BASE_URL}/session`, {
          method: 'DELETE',
          headers: {
            Authorization: `Bearer ${accessToken}`,
          },
        });
      } catch {
        // Ignore network failures during logout cleanup.
      }
    }

    get().clearSession();
  },

  clearSession: () => {
    localStorage.removeItem(storageKey);
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

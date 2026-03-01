const SHARED_SESSION_KEY = 'sentinel.araios.auth.session';

function parseSession() {
  const raw = localStorage.getItem(SHARED_SESSION_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return parsed;
  } catch {
    return null;
  }
}

export function getToken() {
  const session = parseSession();
  return session?.accessToken || '';
}

export function getRefreshToken() {
  const session = parseSession();
  return session?.refreshToken || '';
}

export function setSession(session) {
  const existing = parseSession() || {};
  localStorage.setItem(
    SHARED_SESSION_KEY,
    JSON.stringify({
      ...existing,
      ...session,
      tokenType: (session && session.tokenType) || existing.tokenType || 'bearer',
    }),
  );
}

export function setToken(token) {
  setSession({ accessToken: token, tokenType: 'bearer' });
}

export function clearToken() {
  localStorage.removeItem(SHARED_SESSION_KEY);
}

export function isAuthenticated() {
  return !!getToken();
}

export async function api(path, opts = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(path, { headers, ...opts });
  if (res.status === 401) {
    clearToken();
    window.location.assign('/');
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || body.detail || 'Request failed');
  }
  return res.json();
}

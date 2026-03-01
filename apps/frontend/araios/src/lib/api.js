let sessionHint = false;

export function getToken() {
  return '';
}

export function getRefreshToken() {
  return '';
}

export function setSession(session) {
  sessionHint = Boolean(session?.accessToken || session?.refreshToken);
}

export function setToken(token) {
  sessionHint = Boolean(token);
}

export function clearToken() {
  sessionHint = false;
}

export function isAuthenticated() {
  return sessionHint;
}

export async function checkSession() {
  const res = await fetch('/platform/auth/me', { credentials: 'include' });
  sessionHint = res.ok;
  return sessionHint;
}

async function tryRefresh() {
  const res = await fetch('/platform/auth/refresh', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });
  sessionHint = res.ok;
  return res.ok;
}

export async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };

  let res = await fetch(path, { ...opts, headers, credentials: 'include' });
  if (res.status === 401) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      res = await fetch(path, { ...opts, headers, credentials: 'include' });
    }
  }
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

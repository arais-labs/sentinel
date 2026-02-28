export interface JwtClaims {
  sub?: string;
  role?: string;
  agent_id?: string;
  exp?: number;
}

export function decodeJwt(token: string): JwtClaims {
  const parts = token.split('.');
  if (parts.length !== 3) {
    return {};
  }

  try {
    const normalized = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    const decoded = atob(padded);
    const payload = JSON.parse(decoded) as JwtClaims;
    return payload;
  } catch {
    return {};
  }
}

export function tokenExpiresSoon(token: string, skewSeconds = 30): boolean {
  const claims = decodeJwt(token);
  if (!claims.exp) {
    return false;
  }

  const cutoff = Math.floor(Date.now() / 1000) + skewSeconds;
  return claims.exp <= cutoff;
}

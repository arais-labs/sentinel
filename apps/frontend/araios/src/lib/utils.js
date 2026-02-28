export const toLocalDateTimeValue = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const tzOffsetMs = d.getTimezoneOffset() * 60000;
  return new Date(d.getTime() - tzOffsetMs).toISOString().slice(0, 16);
};

export const shortDate = (iso) => {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const now = new Date();
  const diffMs = d.getTime() - now.getTime();
  const abs = Math.abs(diffMs);
  if (abs < 60000) return 'Now';
  if (abs < 3600000) {
    const m = Math.round(abs / 60000);
    return diffMs > 0 ? `in ${m}m` : `${m}m ago`;
  }
  if (abs < 86400000) {
    const h = Math.round(abs / 3600000);
    return diffMs > 0 ? `in ${h}h` : `${h}h ago`;
  }
  return d.toLocaleDateString('en', { month: 'short', day: 'numeric' });
};

export const fmtDate = (iso) => {
  if (!iso) return '\u2014';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '\u2014';
  return d.toLocaleDateString('en', { month: 'short', day: 'numeric', year: 'numeric' });
};

export const initials = (name) => {
  if (!name) return '?';
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
};

export const avatarHue = (name) => {
  let h = 0;
  for (const c of name || 'X') h = (h * 31 + c.charCodeAt(0)) & 0xffff;
  return h % 360;
};

export const adjustHeight = (el) => {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
};

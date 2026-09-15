import type { Worktree } from './types';

export type WorktreeSort = 'smart' | 'recent' | 'branch' | 'folder' | 'visited';
export type WorktreeKind = 'all' | 'branch' | 'detached';
export type WorktreeAvailability = 'all' | 'available' | 'unavailable';
type Field = 'any' | 'branch' | 'path' | 'commit' | 'is';
export type SearchTerm = { field: Field; value: string; exclude: boolean; phrase: boolean };
export type WorktreeMatch = { tree: Worktree; score: number; approximate: boolean };
const normalize = (text: string) => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
const name = (tree: Worktree) => tree.path.split('/').filter(Boolean).at(-1) ?? '';
const words = (text: string) => text.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
const states = ['branch', 'detached', 'current', 'available', 'unavailable', 'locked'] as const;

export function parseWorktreeQuery(query: string): { terms: SearchTerm[]; error?: string } {
  const terms: SearchTerm[] = [];
  // Quoted phrases may contain spaces; a leading minus excludes that term.
  const tokens = query.match(/(?:[^\s"]|"[^"]*"?)+/g) ?? [];
  for (let token of tokens) {
    const exclude = token.startsWith('-');
    if (exclude) token = token.slice(1);
    const scoped = token.match(/^(branch|path|commit|is):(.*)$/i);
    const field = (scoped?.[1].toLowerCase() ?? 'any') as Field;
    const raw = scoped?.[2] ?? token;
    const value = normalize(raw.replace(/^"|"$/g, ''));
    if (!value) continue;
    if (field === 'is' && !states.includes(value as typeof states[number])) {
      return { terms, error: `Unknown filter “is:${value}”. Use ${states.join(', ')}.` };
    }
    terms.push({ field, value, exclude, phrase: raw.startsWith('"') });
  }
  return { terms };
}

// Bounded Damerau–Levenshtein distance: insertion, deletion, replacement and
// adjacent transposition. Only used for words of similar length, after exact
// matching, so short queries don't produce a wall of unrelated results.
function nearWord(a: string, b: string, limit: number): boolean {
  if (Math.abs(a.length - b.length) > limit) return false;
  const rows: number[][] = [Array.from({ length: b.length + 1 }, (_, i) => i)];
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(row[j - 1] + 1, rows[i - 1][j] + 1, rows[i - 1][j - 1] + Number(a[i - 1] !== b[j - 1]));
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) row[j] = Math.min(row[j], rows[i - 2][j - 2] + 1);
    }
    rows.push(row);
  }
  return rows[a.length][b.length] <= limit;
}

function textMatch(text: string, term: SearchTerm): { score: number; approximate: boolean } | null {
  const value = normalize(text);
  const query = term.value;
  if (value === query) return { score: 400, approximate: false };
  const at = value.indexOf(query);
  if (at >= 0) return { score: (at === 0 ? 240 : /[^\p{L}\p{N}]/u.test(value[at - 1]) ? 180 : 120) - Math.min(at, 40), approximate: false };
  if (term.phrase || term.field === 'commit' || query.length < 3 || query.length > 48) return null;
  const parts = words(value);
  const initials = parts.map(word => word[0]).join('');
  if (initials.startsWith(query)) return { score: 95, approximate: true };
  if (parts.some(word => nearWord(query, word, query.length >= 7 ? 2 : 1))) return { score: 65, approximate: true };
  // Compact abbreviations such as “brmon” can match “broker-monitor”.
  let position = -1;
  let first = -1;
  for (const character of query) {
    position = value.indexOf(character, position + 1);
    if (position < 0) return null;
    if (first < 0) first = position;
  }
  return position - first <= query.length * 3 ? { score: 35, approximate: true } : null;
}

function stateMatch(tree: Worktree, value: string): boolean {
  switch (value) {
    case 'branch': return Boolean(tree.branch);
    case 'detached': return !tree.branch;
    case 'current': return tree.current;
    case 'available': return tree.available;
    case 'unavailable': return !tree.available;
    case 'locked': return tree.locked;
    default: return false;
  }
}

export function searchWorktrees(trees: Worktree[], options: {
  query: string; kind: WorktreeKind; availability: WorktreeAvailability;
  sort: WorktreeSort; visited?: Record<string, number>;
}): { matches: WorktreeMatch[]; error?: string } {
  const parsed = parseWorktreeQuery(options.query);
  if (parsed.error) return { matches: [], error: parsed.error };
  const matches: WorktreeMatch[] = [];
  for (const tree of trees) {
    if (options.kind === 'branch' && !tree.branch || options.kind === 'detached' && tree.branch) continue;
    if (options.availability === 'available' && !tree.available || options.availability === 'unavailable' && tree.available) continue;
    let score = 0;
    let approximate = false;
    let included = true;
    for (const term of parsed.terms) {
      const fields = term.field === 'branch' ? [tree.branch ?? ''] : term.field === 'path' ? [tree.path]
        : term.field === 'commit' ? [tree.head ?? ''] : [tree.branch ?? '', name(tree), tree.path, tree.head ?? ''];
      const match = term.field === 'is' ? (stateMatch(tree, term.value) ? { score: 0, approximate: false } : null)
        : fields.map(field => textMatch(field, term.exclude ? { ...term, phrase: true } : term)).filter(value => value !== null).sort((a, b) => b.score - a.score)[0];
      if (term.exclude ? Boolean(match) : !match) { included = false; break; }
      if (match && !term.exclude) { score += match.score; approximate ||= match.approximate; }
    }
    if (included) matches.push({ tree, score, approximate });
  }
  const textual = parsed.terms.some(term => term.field !== 'is' && !term.exclude);
  matches.sort((a, b) => {
    if (options.sort === 'smart' && textual && a.score !== b.score) return b.score - a.score;
    // Pin the current checkout only when it doesn't displace a better match.
    if (a.tree.current !== b.tree.current) return a.tree.current ? -1 : 1;
    if (options.sort === 'smart' || options.sort === 'recent') {
      const recent = (b.tree.last_commit_at ?? 0) - (a.tree.last_commit_at ?? 0);
      if (recent) return recent;
    }
    if (options.sort === 'visited') {
      const visited = (options.visited?.[b.tree.path] ?? 0) - (options.visited?.[a.tree.path] ?? 0);
      if (visited) return visited;
    }
    const labelA = options.sort === 'folder' ? name(a.tree) : a.tree.branch ?? name(a.tree);
    const labelB = options.sort === 'folder' ? name(b.tree) : b.tree.branch ?? name(b.tree);
    return labelA.localeCompare(labelB, undefined, { numeric: true, sensitivity: 'base' }) || a.tree.path.localeCompare(b.tree.path);
  });
  return { matches };
}

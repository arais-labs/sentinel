const logos = import.meta.glob<string>('../../assets/tool-logos/*.svg', { eager: true, query: '?url', import: 'default' });
export const toolLogos: Partial<Record<string, string>> = Object.fromEntries(Object.entries(logos).map(([file, url]) => [file.split('/').pop()!.replace('.svg', ''), url]));

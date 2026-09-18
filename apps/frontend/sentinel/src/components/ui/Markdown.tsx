import { ArrowUpRight, Check, Copy } from 'lucide-react';
import { isValidElement, useEffect, useState } from 'react';
import type { HTMLAttributes, ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import { PreviewLink } from './PreviewLink';
import { previewTargetPath } from '../../../../../desktop/sentinel/src/shared/preview';

interface MarkdownProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  content: string;
  compact?: boolean;
  muted?: boolean;
  invert?: boolean;
}

function isExternalHref(href: string | undefined): boolean {
  if (!href) return false;
  return href.startsWith('http://') || href.startsWith('https://') || href.startsWith('//');
}

const markdownComponents: Components = {
  a: ({ href, children, ...props }) => {
    const external = isExternalHref(href);
    const text = extractNodeText(children);
    const label = external && text === href ? text.replace(/^https?:\/\//, '') : children;
    const content = <>{label}{external && <ArrowUpRight className="markdown-link-icon" size={11} aria-hidden="true" />}</>;
    if (href && previewTargetPath(href)) return <PreviewLink href={href} title={href} {...props}>{content}</PreviewLink>;
    if (external) {
      return <a href={href} title={href} target="_blank" rel="noopener noreferrer" {...props}>{content}</a>;
    }
    return (
      <a href={href} {...props}>
        {children}
      </a>
    );
  },
  thead: ({ children, ...props }) => extractNodeText(children).trim() ? <thead {...props}>{children}</thead> : null,
  table: ({ children, ...props }) => (
    <div className="markdown-table-wrap">
      <table {...props}>{children}</table>
    </div>
  ),
  pre: ({ children, ...props }) => {
    let language = 'text';
    if (isValidElement(children)) {
      const childProps = children.props as { className?: string } | undefined;
      const className = childProps?.className ?? '';
      const match = /language-([\w-]+)/.exec(className);
      if (match?.[1]) {
        language = match[1];
      }
    }

    return <CodeBlockShell language={language} props={props}>{children}</CodeBlockShell>;
  },
};

function extractNodeText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractNodeText).join('');
  if (isValidElement(node)) {
    const props = node.props as { children?: ReactNode } | undefined;
    return extractNodeText(props?.children ?? '');
  }
  return '';
}

function CodeBlockShell({
  language,
  props,
  children,
}: {
  language: string;
  props: HTMLAttributes<HTMLPreElement>;
  children: ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const code = extractNodeText(children).replace(/\n$/, '');

  useEffect(() => {
    if (!copied) return undefined;
    const timer = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function handleCopy() {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="markdown-pre-shell">
      <div className="markdown-pre-bar flex items-center justify-between gap-3">
        <span>{language}</span>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[10px] font-bold uppercase tracking-[0.12em] text-(--text-muted) transition-colors hover:text-(--text-primary) hover:bg-white/10"
          title="Copy code"
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre {...props}>{children}</pre>
    </div>
  );
}

export function Markdown({
  content,
  className = '',
  compact = false,
  muted = false,
  invert = false,
  ...rest
}: MarkdownProps) {
  const classes = [
    'markdown-body',
    compact ? 'markdown-compact' : '',
    muted ? 'markdown-muted' : '',
    invert ? 'markdown-invert' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes} {...rest}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: false }]]}
        rehypePlugins={[rehypeKatex, rehypeHighlight]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

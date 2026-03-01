import { isValidElement } from 'react';
import type { HTMLAttributes } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

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
    if (isExternalHref(href)) {
      return (
        <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
          {children}
        </a>
      );
    }
    return (
      <a href={href} {...props}>
        {children}
      </a>
    );
  },
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

    return (
      <div className="markdown-pre-shell">
        <div className="markdown-pre-bar">{language}</div>
        <pre {...props}>{children}</pre>
      </div>
    );
  },
};

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
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, rehypeHighlight]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

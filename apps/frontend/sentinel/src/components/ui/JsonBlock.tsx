interface JsonBlockProps {
  value: string;
  className?: string;
}

export function JsonBlock({ value, className = '' }: JsonBlockProps) {
  return (
    <pre
      className={`m-0 max-h-[300px] overflow-auto rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-3 ${className}`}
    >
      <code className="font-mono text-[12px] leading-relaxed text-[color:var(--text-secondary)] whitespace-pre-wrap">
        {value}
      </code>
    </pre>
  );
}

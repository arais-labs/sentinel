interface JsonBlockProps {
  value: string;
  className?: string;
}

export function JsonBlock({ value, className = '' }: JsonBlockProps) {
  return (
    <pre
      className={`m-0 max-h-[300px] overflow-auto rounded-lg border border-(--border-subtle) bg-(--surface-1) p-3 ${className}`}
    >
      <code className="font-mono text-[12px] leading-relaxed text-(--text-secondary) whitespace-pre-wrap">
        {value}
      </code>
    </pre>
  );
}

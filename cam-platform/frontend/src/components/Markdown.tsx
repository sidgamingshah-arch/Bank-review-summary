import { useMemo } from 'react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

interface Props {
  content: string;
  className?: string;
}

/** Renders markdown (all section bodies / chat replies are markdown per the contract). */
export function Markdown({ content, className }: Props) {
  const html = useMemo(() => {
    const raw = marked.parse(content ?? '', { async: false, gfm: true, breaks: false });
    return DOMPurify.sanitize(raw);
  }, [content]);

  return <div className={`markdown-body${className ? ` ${className}` : ''}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

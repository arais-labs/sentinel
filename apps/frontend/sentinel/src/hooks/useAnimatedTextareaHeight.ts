import { useLayoutEffect, type RefObject } from 'react';

function resize(textarea: HTMLTextAreaElement) {
  const current = textarea.getBoundingClientRect().height;
  const scrollTop = textarea.scrollTop;
  const style = getComputedStyle(textarea);
  const minimum = parseFloat(style.minHeight) || 0;
  const maximum = parseFloat(style.maxHeight) || Infinity;
  const previousTransition = textarea.style.transition;
  // Measure natural content height, including shrinking after deleting or sending.
  // Restore the painted height before transitioning to the new measurement.
  textarea.style.transition = 'none';
  textarea.style.height = '0px';
  const target = Math.min(maximum, Math.max(minimum, textarea.scrollHeight));
  textarea.style.height = `${current}px`;
  void textarea.offsetHeight;
  textarea.style.transition = previousTransition;
  textarea.style.height = `${target}px`;
  textarea.scrollTop = scrollTop;
}

export function useAnimatedTextareaHeight(ref: RefObject<HTMLTextAreaElement | null>, value: string) {
  useLayoutEffect(() => {
    if (ref.current) resize(ref.current);
  }, [ref, value]);
  useLayoutEffect(() => {
    const textarea = ref.current;
    if (!textarea) return;
    let width = textarea.clientWidth;
    const observer = new ResizeObserver(() => {
      if (textarea.clientWidth === width) return;
      width = textarea.clientWidth;
      resize(textarea);
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [ref]);
}

import { Children, Fragment, cloneElement, isValidElement, type ReactNode } from 'react';

/** Keep portaled overlays out of the toolbar's sizing and control order. */
export function splitPaneActions(node: ReactNode): { items: ReactNode[]; overlays: ReactNode[] } {
  const overlays: ReactNode[] = [];
  const controls = (children: ReactNode): ReactNode[] => Children.toArray(children).flatMap((child): ReactNode[] => {
    if (typeof child === 'object' && child !== null && '$$typeof' in child && child.$$typeof === Symbol.for('react.portal')) {
      overlays.push(child);
      return [];
    }
    if (!isValidElement<{ children?: ReactNode; className?: string }>(child)) return [child];
    const layout = child.type === Fragment || (child.type === 'div'
      && Object.keys(child.props).every((key) => key === 'children' || key === 'className')
      && /\bflex\b/.test(child.props.className ?? ''));
    return layout ? controls(child.props.children) : [child];
  });
  // Children.toArray encodes sibling positions in keys. An inserted portal must
  // not change a control's identity and remount its button or lose its focus.
  const items = controls(node).map((item, index) => isValidElement(item) ? cloneElement(item, { key: index }) : item);
  return { items, overlays };
}

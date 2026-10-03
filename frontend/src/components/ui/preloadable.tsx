import { createElement, lazy, useRef, type ComponentProps, type ComponentType } from 'react';

/** Reuse loaded code without showing another fallback; keep each mounted editor's type stable. */
export function preloadable<Component extends ComponentType<any>>(loader: () => Promise<{ default: Component }>) {
  let loaded: Component | undefined;
  let pending: Promise<{ default: Component }> | undefined;
  const load = () => pending ??= loader().then(module => { loaded = module.default; return module; });
  const Deferred = lazy(load);
  function Preloadable(props: ComponentProps<Component>) {
    const component = useRef<ComponentType<any>>(loaded ?? Deferred).current;
    return createElement(component, props);
  }
  return Object.assign(Preloadable, { preload: async () => { await load(); } });
}

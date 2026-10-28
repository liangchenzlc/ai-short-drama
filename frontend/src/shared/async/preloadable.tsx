import { createElement, lazy, useRef, type ComponentType } from 'react';
/** Reuse loaded code without showing another fallback; keep each mounted editor's type stable. */
export function preloadable<Component extends ComponentType<never>>(loader: () => Promise<{ default: Component }>) {
  type Props = Component extends ComponentType<infer Value> ? Value & object : never;
  let loaded: ComponentType<Props> | undefined;
  let pending: Promise<{ default: ComponentType<Props> }> | undefined;
  const load = () =>
    (pending ??= loader().then((module) => {
      // Props are extracted from this exact loader's component, including required fields.
      loaded = module.default as ComponentType<Props>;
      return { default: loaded };
    }));
  const Deferred = lazy(load);
  function Preloadable(props: Props) {
    const component = useRef<ComponentType<Props>>(loaded ?? Deferred).current;
    return createElement(component, props);
  }
  return Object.assign(Preloadable, {
    preload: async () => {
      await load();
    },
  });
}

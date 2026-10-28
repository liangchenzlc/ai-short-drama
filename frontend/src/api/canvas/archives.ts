import type { CanvasArchiveBridge } from '@host/types/canvas/archives';
let bridge: Promise<CanvasArchiveBridge> | undefined;

/** 复用独立画布实现；两包不混用别名、依赖或React root。 */
export function loadCanvasArchiveBridge(): Promise<CanvasArchiveBridge> {
  if (!bridge) {
    const path = import.meta.env.DEV ? '/canvas-app/src/services/host-archive-entry.ts' : '/canvas-app/host-archive.js';
    bridge = import(/* @vite-ignore */ path)
      .then((module) => module as CanvasArchiveBridge)
      .catch((cause) => {
        bridge = undefined;
        throw new Error('无法加载画布导入导出，请稍后重试', { cause });
      });
  }
  return bridge;
}

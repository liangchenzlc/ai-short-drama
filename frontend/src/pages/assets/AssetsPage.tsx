import type { AssetKind } from '../../features/assets/asset-model';
import { AssetLibraryPanel } from '../../features/assets/AssetLibraryPanel';

function downloadLegacy() {
  const content = localStorage.getItem('avi-global-assets-v1') ?? '[]';
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'legacy-global-assets.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function AssetsPage({ kind: _kind }: { kind: AssetKind }) {
  return <section className="studio-page" aria-label="全局素材库"><AssetLibraryPanel scope={{ kind: 'global' }} title="全局角色、场景与道具" initialKind={_kind} legacyDownload={downloadLegacy}/></section>;
}

import type { AssetKind } from '../../features/assets/asset-model';
import { AssetLibraryPanel } from '../../features/assets/AssetLibraryPanel';
import { useNavigate } from 'react-router-dom';

function downloadLegacy() {
  const content = localStorage.getItem('avi-global-assets-v1') ?? '[]';
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'legacy-global-assets.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function AssetsPage({ kind: _kind }: { kind: AssetKind }) {
  const navigate = useNavigate();
  return <section className="studio-page global-assets-page" aria-label="全局素材库"><div className="studio-page-head"><div><h1>全局素材库</h1><p>集中整理角色、场景与道具，在不同项目中复用。</p></div></div><AssetLibraryPanel scope={{ kind: 'global' }} title="共享素材" initialKind={_kind} onKindChange={kind => navigate(`/assets/${kind}`)} legacyDownload={downloadLegacy}/></section>;
}

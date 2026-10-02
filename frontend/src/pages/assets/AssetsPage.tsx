import { useAuth } from '../../features/auth/AuthSession';
import type { AssetKind } from '../../features/assets/asset-model';
import { AssetLibraryPanel } from '../../features/assets/AssetLibraryPanel';
import { useNavigate } from 'react-router-dom';
import { PageHeader } from '../../components/ui/Workspace';

function downloadLegacy() {
  const content = localStorage.getItem('avi-global-assets-v1') ?? '[]';
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'legacy-global-assets.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function AssetsPage({ kind: _kind }: { kind: AssetKind }) {
  const navigate = useNavigate();
  const auth = useAuth();
  const title = auth.enabled ? '个人素材库' : '全局素材库';
  return <section className="studio-page global-assets-page" aria-label={title}><PageHeader title={title} description={auth.enabled ? '整理自己的角色、场景与道具，导入项目时创建独立副本。' : '集中整理角色、场景与道具，在不同项目中复用。'} /><AssetLibraryPanel scope={{ kind: 'global' }} title={auth.enabled ? '我的素材' : '共享素材'} initialKind={_kind} onKindChange={kind => navigate(`/assets/${kind}`)} legacyDownload={auth.enabled ? undefined : downloadLegacy}/></section>;
}

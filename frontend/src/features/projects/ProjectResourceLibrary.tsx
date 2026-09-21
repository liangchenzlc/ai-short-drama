import { AssetLibraryPanel } from '../assets/AssetLibraryPanel';

export function ProjectResourceLibrary({ projectId }: { projectId: string }) {
  function downloadLegacy() {
    const content = localStorage.getItem(`avi-project-resources-v1-${projectId}`) ?? '[]';
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `legacy-project-${projectId}-assets.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <AssetLibraryPanel scope={{ kind: 'project', projectId }} importFrom={{ kind: 'global' }} title="项目资源库" legacyDownload={downloadLegacy}/>;
}

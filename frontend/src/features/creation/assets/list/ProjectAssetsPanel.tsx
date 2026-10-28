import { ProjectAssetWorkspace } from '@host/features/creation/assets/editor/ProjectAssetWorkspace';
export function ProjectAssetsPanel(props: Parameters<typeof ProjectAssetWorkspace>[0] & { variant?: 'standard' }) {
  return <ProjectAssetWorkspace {...props} />;
}

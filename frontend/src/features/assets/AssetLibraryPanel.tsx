import { EpisodeAssetWorkspace } from './EpisodeAssetWorkspace';

export function AssetLibraryPanel(props: Parameters<typeof EpisodeAssetWorkspace>[0] & { variant?: 'standard' }) {
  return <EpisodeAssetWorkspace {...props}/>;
}

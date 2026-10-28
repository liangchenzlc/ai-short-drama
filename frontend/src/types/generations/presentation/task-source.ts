export type TaskSourceTarget =
  | {
      kind: 'project';
      project_id: string;
      stage: 'source' | 'assets' | 'storyboard' | 'assembly';
    }
  | { kind: 'asset'; asset_id: string };

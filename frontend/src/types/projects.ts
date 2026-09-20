export interface RecentProject {
  projectId: string;
  name: string;
  lastOpenedAt: string;
}

export interface WebProject extends RecentProject {
  aspect: '16:9' | '9:16';
}

export interface ProjectSession {
  projectId: string;
  projectSessionId: string;
  mode: 'read' | 'write';
  project: WebProject;
}

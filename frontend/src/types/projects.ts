export interface RecentProject {
  projectId: string;
  name: string;
  lastOpenedAt: string;
}

export interface WebProject extends RecentProject {
}

export interface ProjectSession {
  projectId: string;
  projectSessionId: string;
  mode: 'read' | 'write';
  project: WebProject;
}

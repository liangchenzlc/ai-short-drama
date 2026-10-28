export interface Account {
  id: string;
  username: string;
  display_name: string;
  email: string;
  email_verified: boolean;
}

export interface Session {
  enabled: boolean;
  user: Account | null;
  loading: boolean;
  error: string;
  expired: boolean;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

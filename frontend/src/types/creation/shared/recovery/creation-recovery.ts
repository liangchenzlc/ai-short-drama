export type RecoveryStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export interface StoredDraft<T> {
  format: 1;
  base_version: string;
  document: T;
}

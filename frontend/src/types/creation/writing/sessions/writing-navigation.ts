export interface NavigationBarrier {
  hasUnsettled(): boolean;
  flush(): Promise<boolean>;
  hasMemoryOnlyChanges?(): boolean;
}

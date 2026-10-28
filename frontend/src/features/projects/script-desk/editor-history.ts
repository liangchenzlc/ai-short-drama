interface Entry { text: string; start: number; end: number }

/** In-memory editing history; an AI replacement is always a separate transaction. */
export class ScriptHistory {
  private entries: Entry[];
  private cursor = 0;
  private lastTypedAt = 0;
  constructor(text: string) { this.entries = [{ text, start: 0, end: 0 }]; }
  get text() { return this.entries[this.cursor].text; }
  get canUndo() { return this.cursor > 0; }
  get canRedo() { return this.cursor < this.entries.length - 1; }
  remember(start: number, end: number) { Object.assign(this.entries[this.cursor], { start, end }); }
  record(text: string, start: number, end: number, typing = false, now = Date.now()) {
    if (text === this.text) return;
    const coalesce = typing && this.lastTypedAt > 0 && now - this.lastTypedAt < 700 && this.cursor === this.entries.length - 1;
    this.entries = this.entries.slice(0, this.cursor + 1);
    if (coalesce) this.entries[this.cursor] = { text, start, end };
    else { this.entries.push({ text, start, end }); ++this.cursor; }
    this.lastTypedAt = typing ? now : 0;
    if (this.entries.length > 100) { this.entries.shift(); --this.cursor; }
  }
  move(direction: -1 | 1): Entry | null {
    const next = this.cursor + direction;
    if (next < 0 || next >= this.entries.length) return null;
    this.cursor = next; this.lastTypedAt = 0;
    return this.entries[this.cursor];
  }
}

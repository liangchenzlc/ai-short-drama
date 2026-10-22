import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

// 运行真实日记、版本与文档比较，仅替换账号、IndexedDB及Store边界；不代表真实持久化验收。
const stubs = {
    "@/lib/localforage-storage": "export const localForageStorageForScope = scope => globalThis.journalBoundary.storage(scope);",
    "@/lib/user-scope": "export const getActiveUserScope = () => globalThis.journalBoundary.scope;",
    "@/stores/canvas/use-canvas-store": "export const recordCanvasDocumentBase = () => {};",
};
const bundled = await build({
    entryPoints: [fileURLToPath(new URL("../../src/services/canvas-operation-journal.ts", import.meta.url))],
    tsconfig: fileURLToPath(new URL("../../tsconfig.json", import.meta.url)),
    bundle: true, write: false, format: "esm", platform: "node", target: "node22",
    plugins: [{ name: "journal-boundaries", setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => args.path in stubs ? { path: args.path, namespace: "controlled" } : undefined);
        builder.onLoad({ filter: /.*/, namespace: "controlled" }, args => ({ contents: stubs[args.path], loader: "js" }));
    } }],
});
const journal = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);
function setup() {
    journal.resetCanvasOperationJournalMemory();
    const values = new Map();
    globalThis.journalBoundary = { scope: "user:a", fail: false, storage: scope => ({
        getItem: async key => values.get(`${scope}:${key}`) ?? null,
        setItem: async (key, value) => { if (globalThis.journalBoundary.fail) throw new Error("存储写入失败"); values.set(`${scope}:${key}`, value); },
        removeItem: async key => values.delete(`${scope}:${key}`),
    }) };
    return values;
}
const project = (count = 2) => ({ id: "canvas", revision: "9007199254740993", title: "性能日记",
    nodes: Array.from({ length: count }, (_, index) => ({ id: `n${index}`, type: "text", title: "正文", position: { x: index, y: 0 }, width: 240, height: 160, metadata: { content: "保存正文" } })), connections: [], chatSessions: [] });

test("5000节点只读保存状态不复制正文，也不暴露完整快照", async () => {
    setup(); await journal.recordConfirmedCanvasCommit(project(5000));
    const clone = globalThis.structuredClone; let calls = 0;
    globalThis.structuredClone = value => { calls++; return clone(value); };
    try {
        for (let index = 0; index < 100; index++) {
            assert.deepEqual(journal.peekCanvasOperationJournalStatus("canvas"), { confirmedRevision: "9007199254740993", hasConfirmedSnapshot: true, hasInFlight: false, hasPendingProjection: false, resourceAliases: {} });
        }
        assert.equal(calls, 0);
    } finally { globalThis.structuredClone = clone; }
});

test("状态与资源来源按本人隔离，返回别名不能修改日记", async () => {
    setup(); await journal.recordConfirmedCanvasCommit(project());
    await journal.updateCanvasOperationJournal("canvas", "user:a", current => ({ ...current, resourceAliases: { "200": ["100"] } }));
    assert.equal(journal.peekCanvasOperationJournalStatus("canvas", "user:b"), undefined);
    const status = journal.peekCanvasOperationJournalStatus("canvas");
    status.confirmedRevision = "1"; status.resourceAliases["200"].push("999");
    assert.equal(journal.peekCanvasOperationJournalStatus("canvas").confirmedRevision, "9007199254740993");
    assert.deepEqual(journal.peekCanvasOperationJournalStatus("canvas").resourceAliases, { "200": ["100"] });
});

test("完整快照仍独立，加载或调用者编辑不能污染已确认正文", async () => {
    setup(); const input = project(); await journal.recordConfirmedCanvasCommit(input);
    input.nodes[0].metadata.content = "新草稿";
    const loaded = await journal.loadCanvasOperationJournal("canvas");
    assert.equal(loaded.confirmedSnapshot.nodes[0].metadata.content, "保存正文");
    loaded.confirmedSnapshot.nodes[0].metadata.content = "修改副本";
    const peeked = journal.peekCanvasOperationJournal("canvas");
    assert.equal(peeked.confirmedSnapshot.nodes[0].metadata.content, "保存正文");
    peeked.confirmedSnapshot.nodes.splice(0);
    assert.equal(journal.peekCanvasOperationJournal("canvas").confirmedSnapshot.nodes.length, 2);
});

test("未落盘的写入失败保留旧状态与原正文", async () => {
    setup(); await journal.recordConfirmedCanvasCommit(project()); globalThis.journalBoundary.fail = true;
    await assert.rejects(journal.updateCanvasOperationJournal("canvas", "user:a", current => ({ ...current, confirmedRevision: "9007199254740994" })), /存储写入失败/);
    assert.equal(journal.peekCanvasOperationJournalStatus("canvas").confirmedRevision, "9007199254740993");
    assert.equal(journal.peekCanvasOperationJournal("canvas").confirmedSnapshot.revision, "9007199254740993");
});

test("在途提交重载保留原键和正文，匹配回执才清除", async () => {
    setup(); const input = project(); await journal.recordConfirmedCanvasCommit(input);
    await journal.updateCanvasOperationJournal("canvas", "user:a", current => ({ ...current, inFlight: { operationId: "same-key", expectedRevision: input.revision, payload: { canvasId: input.id, expectedRevision: input.revision, document: input } } }));
    assert.equal(journal.peekCanvasOperationJournalStatus("canvas").hasInFlight, true);
    journal.resetCanvasOperationJournalMemory();
    const reloaded = await journal.loadCanvasOperationJournal("canvas");
    assert.equal(reloaded.inFlight.operationId, "same-key");
    assert.deepEqual(reloaded.inFlight.payload.document, input);
    await journal.recordConfirmedCanvasCommit(input, "user:a", { ackOperationId: "other-key" });
    assert.equal(journal.peekCanvasOperationJournalStatus("canvas").hasInFlight, true);
    await journal.recordConfirmedCanvasCommit(input, "user:a", { ackOperationId: "same-key" });
    assert.equal(journal.peekCanvasOperationJournalStatus("canvas").hasInFlight, false);
});

test("未完成投影保持保存门禁，身份不匹配不能清除", async () => {
    setup(); const input = project(); await journal.recordConfirmedCanvasCommit(input);
    await journal.recordConfirmedCanvasCommit({ ...input, revision: "9007199254740994", title: "新版本" }, "user:a", { unflushedProjection: true });
    const pending = journal.peekCanvasOperationJournal("canvas").pendingProjection;
    assert.equal(journal.peekCanvasOperationJournalStatus("canvas").hasPendingProjection, true);
    await journal.clearCanvasPendingProjection("canvas", "user:a", "wrong-identity");
    assert.equal(journal.peekCanvasOperationJournalStatus("canvas").hasPendingProjection, true);
    await journal.clearCanvasPendingProjection("canvas", "user:a", pending.identity);
    assert.equal(journal.peekCanvasOperationJournalStatus("canvas").hasPendingProjection, false);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import test from "node:test";
import vm from "node:vm";

const dependency = createRequire(resolve(process.cwd(), "package.json"));
const ts = dependency("typescript");
const referenceRoot = process.cwd();
const sourceRoot = process.env.CANVAS_SOURCE_FEEDBACK_ROOT || referenceRoot;
const source = path => readFileSync(resolve(sourceRoot, path), "utf8");
const reference = path => readFileSync(resolve(referenceRoot, path), "utf8");

function compiled(text, dependencies) {
    const exports = {};
    const code = ts.transpileModule(text, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText;
    vm.runInNewContext(code, { exports, require: name => dependencies[name] || {}, Error, AbortController, structuredClone });
    return exports;
}

// 执行整个真实上传Hook和真实scope/owner守卫；只替换React、HTTP、节点工厂与图状态。
function fixture() {
    let scope = { userScope: "101", epoch: 1 };
    const cells = [];
    let cursor = 0;
    const pendingEffects = [];
    const effectDeps = new Map();
    const calls = { reads: [], messages: [], selected: [] };
    const nodesRef = { current: [] };
    const react = {
        useCallback: callback => callback,
        useRef: initial => {
            const index = cursor++;
            if (!cells[index]) cells[index] = { current: initial };
            return cells[index];
        },
        useState: initial => {
            const index = cursor++;
            if (!(index in cells)) cells[index] = initial;
            return [cells[index], update => { cells[index] = typeof update === "function" ? update(cells[index]) : update; }];
        },
        useEffect: (effect, deps) => {
            const index = cursor++;
            const previous = effectDeps.get(index);
            if (!previous || deps.some((item, slot) => item !== previous[slot])) {
                effectDeps.set(index, deps);
                pendingEffects.push(effect);
            }
        },
    };
    const scopeModule = compiled(reference("src/lib/user-scope-guard.ts"), {
        "@/lib/user-scope": { getActiveUserScope: () => scope.userScope, getActiveUserScopeEpoch: () => scope.epoch },
    });
    const epochModule = compiled(reference("src/pages/canvas/canvas-owner-epoch.ts"), {
        react, "@/lib/user-scope": { getActiveUserScope: () => scope.userScope },
    });
    const lifetime = epochModule.createCanvasOwnerLifetime();
    const mountedRef = { current: true };
    const ownerModule = compiled(reference("src/pages/canvas/canvas-upload-ownership.ts"), {
        "@/pages/canvas/canvas-owner-epoch": epochModule,
        "@/lib/user-scope-guard": scopeModule,
    });
    const sourceModule = compiled(reference("src/services/host-source-project.ts"), {});
    let nodeId = 0;
    const hookModule = compiled(source("src/pages/canvas/use-canvas-upload.ts"), {
        react, "@tanstack/react-query": { useQueryClient: () => ({}) },
        antd: { App: { useApp: () => ({ message: { error: text => calls.messages.push(text), success: text => calls.messages.push(text) } }) } },
        "@/lib/user-scope-guard": scopeModule,
        "./canvas-owner-epoch": { ...epochModule, useCanvasOwnerLifetime: () => ({ lifetime, mountedRef }) },
        "./canvas-upload-ownership": ownerModule,
        "@/services/host-source-project": sourceModule,
        "@/types/canvas": { CanvasNodeType: { Text: "text" } },
        "@/lib/canvas/canvas-project-domain": { createCanvasNode: (type, position, metadata) => ({ id: `text-${++nodeId}`, type, position, metadata }) },
    });
    const options = {
        canvasId: "canvas-one", domainProjectId: "201", sourceContextKey: "source-one:revision-3",
        readSourceEpisode: async (...args) => {
            calls.reads.push(args);
            return boundary(...args);
        },
        nodesRef, connectionsRef: { current: [] }, selectedNodeIdsRef: { current: new Set() },
        getCanvasCenter: () => ({ x: 10, y: 20 }), screenToCanvas: (x, y) => ({ x, y }),
        setNodes: update => { nodesRef.current = update(nodesRef.current); },
        setConnections: () => {}, setSelectedNodeIds: ids => calls.selected.push([...ids]),
        setSelectedConnectionId: () => {}, setContextMenu: () => {}, setDialogNodeId: () => {},
    };
    const chapter = { id: "301", projectId: "201", title: "字面首章", position: 1, contentVersion: "4" };
    const body = (plainText = "  原文\r\n<script>字面</script>&  ") => ({
        episode: { id: chapter.id, title: chapter.title, position: chapter.position, content_version: chapter.contentVersion, kind: "episode" },
        novel: { state: "present", content_format: "plain_text", plain_text: plainText },
    });
    let boundary = async () => body();
    function render() {
        cursor = 0;
        const result = hookModule.useCanvasUpload(options);
        for (const effect of pendingEffects.splice(0)) effect();
        cursor = 0;
        return hookModule.useCanvasUpload(options);
    }
    return { render, options, chapter, body, calls, nodesRef, mountedRef, lifetime,
        boundary: handler => { boundary = handler; }, scope: value => { scope = value; } };
}

function deferred() {
    let resolvePromise;
    let reject;
    const promise = new Promise((resolveValue, rejectValue) => { resolvePromise = resolveValue; reject = rejectValue; });
    return { promise, resolve: resolvePromise, reject };
}

test("真实章节handler显示loading并逐字插入空与非空正文；成功只清自己的状态", async () => {
    for (const content of ["", "  原文\r\n<script>字面</script>&  "]) {
        const f = fixture(); const response = deferred(); f.boundary(() => response.promise);
        const operation = f.render().handleProjectChapterInsert(f.chapter, { x: 80, y: 90 });
        assert.equal(f.render().sourceChapterStatus?.loading, true);
        assert.equal(f.render().sourceChapterStatus?.chapterId, f.chapter.id);
        assert.equal(f.nodesRef.current.length, 0);
        response.resolve(f.body(content)); await operation;
        assert.equal(f.render().sourceChapterStatus, null);
        const node = f.nodesRef.current[0];
        assert.equal(node.metadata.content, content); assert.equal(node.metadata.prompt, content);
        assert.equal(node.metadata.workflowDescription, "第 1 章");
        assert.equal(node.position.x, 80); assert.equal(node.position.y, 90);
        assert.equal(f.calls.messages.length, 0);
    }
});

test("missing记录和409读取在就近状态保留原因且图/已保存草稿不变，不只依赖隐藏toast", async () => {
    for (const failure of ["missing", "409"]) {
        const f = fixture(); f.nodesRef.current = [{ id: "saved-draft", metadata: { content: "本人的已保存草稿" } }];
        const before = structuredClone(f.nodesRef.current);
        f.boundary(async () => {
            if (failure === "409") throw new Error("HTTP 409 canvas_source_content_conflict");
            return { ...f.body(), novel: { state: "missing", content_format: "plain_text", plain_text: null } };
        });
        await f.render().handleProjectChapterInsert(f.chapter);
        const status = f.render().sourceChapterStatus;
        assert.equal(status?.loading, false);
        assert.match(status?.error || "", failure === "missing" ? /章节正文读取失败：本集尚未保存小说正文/ : /章节正文读取失败：HTTP 409 canvas_source_content_conflict/);
        assert.deepEqual(f.nodesRef.current, before);
        assert.equal(f.calls.reads.length, 1); assert.equal(f.calls.messages.length, 0);
    }
});

test("失败后的明确重试清旧错误、重新读取相同冻结身份并正常插入", async () => {
    const f = fixture(); f.boundary(async () => { throw new Error("HTTP 409"); });
    await f.render().handleProjectChapterInsert(f.chapter);
    assert.match(f.render().sourceChapterStatus.error, /409/);
    const next = deferred(); f.boundary(() => next.promise);
    const retry = f.render().handleProjectChapterInsert(f.chapter);
    assert.equal(f.render().sourceChapterStatus.error, ""); assert.equal(f.render().sourceChapterStatus.loading, true);
    next.resolve(f.body()); await retry;
    assert.equal(f.calls.reads.length, 2);
    assert.equal(f.calls.reads[1][0], f.chapter.id); assert.equal(f.calls.reads[1][1], f.chapter.contentVersion);
    assert.equal(f.nodesRef.current.length, 1); assert.equal(f.render().sourceChapterStatus, null);
});

for (const kind of ["source", "source-return", "canvas", "account", "account-return", "lifetime", "unmount"]) {
    test(`读取期间${kind}失效抑制旧正文和feedback`, async () => {
        const f = fixture(); const response = deferred(); f.boundary(() => response.promise);
        const operation = f.render().handleProjectChapterInsert(f.chapter);
        assert.equal(f.render().sourceChapterStatus.loading, true);
        if (kind === "source") f.options.sourceContextKey = "source-two:revision-4";
        if (kind === "source-return") {
            const original = f.options.sourceContextKey;
            f.options.sourceContextKey = "source-two:revision-4"; f.render();
            f.options.sourceContextKey = original;
        }
        if (kind === "canvas") f.options.canvasId = "canvas-two";
        if (kind === "account") f.scope({ userScope: "102", epoch: 2 });
        if (kind === "account-return") f.scope({ userScope: "101", epoch: 3 });
        if (kind === "lifetime") f.lifetime.invalidate();
        if (kind === "unmount") f.mountedRef.current = false;
        f.render(); response.resolve(f.body()); await operation;
        assert.equal(f.nodesRef.current.length, 0); assert.equal(f.calls.messages.length, 0);
        if (!["lifetime", "unmount"].includes(kind)) assert.equal(f.render().sourceChapterStatus, null);
    });
}

for (const oldResult of ["success", "error"]) {
    test(`并发旧${oldResult}不清空或覆盖新请求的loading与错误`, async () => {
        const f = fixture(); const first = deferred(); const second = deferred(); let read = 0;
        f.boundary(() => ++read === 1 ? first.promise : second.promise);
        const old = f.render().handleProjectChapterInsert(f.chapter);
        const current = f.render().handleProjectChapterInsert({ ...f.chapter, id: "302" });
        const requestId = f.render().sourceChapterStatus.requestId;
        if (oldResult === "success") first.resolve(f.body()); else first.reject(new Error("旧409"));
        await old;
        assert.equal(f.render().sourceChapterStatus.requestId, requestId);
        assert.equal(f.render().sourceChapterStatus.loading, true); assert.equal(f.render().sourceChapterStatus.error, "");
        second.reject(new Error("新409")); await current;
        assert.match(f.render().sourceChapterStatus.error, /新409/);
        assert.equal(f.render().sourceChapterStatus.chapterId, "302");
    });
}

test("读取前来源project不匹配明确失败且零HTTP，不制造节点", async () => {
    const f = fixture(); await f.render().handleProjectChapterInsert({ ...f.chapter, projectId: "999" });
    assert.equal(f.calls.reads.length, 0); assert.equal(f.nodesRef.current.length, 0);
    assert.match(f.render().sourceChapterStatus.error, /所选章节与当前来源项目不匹配/);
});

test("侧栏/独立预览与600首章区域接同一受保护状态，读取失败不自动刷新或恢复全局toast", () => {
    const project = source("src/pages/canvas/project.tsx");
    const sidebar = source("src/components/canvas/canvas-project-sidebar.tsx");
    const entry = source("src/components/canvas/canvas-short-drama-entry.tsx");
    assert.match(project, /sourceContextKey: sourceProject.contextKey/);
    assert.match(project, /source=\{sourceProject\} chapterStatus=\{sourceChapterStatus\}/);
    assert.match(project, /sourceLoading=\{sourceProject.loading\}[\s\S]*sourceError=\{sourceProject.error\}[\s\S]*chapterStatus=\{sourceChapterStatus\}/);
    assert.match(sidebar, /data-canvas-source-chapter-status role=\{chapterError \? "alert" : "status"\}/);
    assert.match(sidebar, /insertError=\{chapterStatus\?\.chapterId === selectedUnit.id && chapterStatus.contentVersion === selectedUnit.content_version \? chapterStatus.error : ""\}/);
    assert.match(sidebar, /loadError=\{selectedUnitQuery.isError/);
    assert.match(sidebar, /hidden w-\[var\(--canvas-sidebar-width\)\][^\n]*lg:flex/);
    assert.match(entry, /data-canvas-source-first-chapter-status role=\{error \? "alert" : "status"\}/);
    assert.match(entry, /disabled=\{!hasChapter \|\| sourceLoading \|\| Boolean\(chapterStatus\?\.loading\)\}/);
    const hook = source("src/pages/canvas/use-canvas-upload.ts");
    const handler = hook.slice(hook.indexOf("const handleProjectChapterInsert ="), hook.indexOf("const handleUploadRequest ="));
    assert.doesNotMatch(handler, /message\.error|message\.success|refreshSourceProject/);
});

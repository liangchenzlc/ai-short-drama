import assert from "node:assert/strict";
import test from "node:test";
import { createHostCanvasContract } from "../src/services/host-canvas-contract.ts";

const canvas = { id: "9007199254740999", project_id: "9007199254741001", source_key: "source-key", row_version: "9007199254741003", title: "画布", created_at: "2026-01-01", updated_at: "2026-01-02" };

test("回收站恢复不先解析已归档画布，重试引用同一本人删除回执", async () => {
    const calls = [];
    const bridge = createHostCanvasContract(async request => { calls.push(request); return canvas; });
    const result = await bridge("post", "/canvas-projects/source-key/recycle-restore", { revision: canvas.row_version });
    await bridge("post", "/canvas-projects/source-key/recycle-restore", { revision: canvas.row_version });
    assert.equal(result.project.workspaceProjectId, canvas.project_id);
    assert.equal(result.project.revision, canvas.row_version);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0], calls[1]);
    assert.equal(calls[0].url, "/canvas-runtime/canvas-projects/source-key/recycle-restore");
    assert.deepEqual(calls[0].data, { archive_key: `canvas-delete:source-key:${canvas.row_version}` });
    assert.equal(calls[0].headers["Idempotency-Key"], `canvas-recycle:source-key:${canvas.row_version}`);
    for (const revision of [undefined, "0", "1.2", "-1"]) {
        await assert.rejects(bridge("post", "/canvas-projects/source-key/recycle-restore", { revision }), /已删除版本/);
    }
    assert.equal(calls.length, 2);
});

test("分页摘要保留本人文件夹，让刷新后的原项目卡片仍在正确分类", async () => {
    const bridge = createHostCanvasContract(async () => ({ items: [{ ...canvas, folder_id: "folder-one", node_count: 0, preview_nodes: [] }], page: 1, page_size: 50, total: 1, has_more: false }));
    const result = await bridge("get", "/canvas-projects");
    assert.equal(result.projects[0].folderId, "folder-one");
    assert.equal(result.projects[0].revision, canvas.row_version);
});

test("首次创建将笔画清单放在事务请求边界，同文重试而不混入共享图", async () => {
    const calls = [];
    const bridge = createHostCanvasContract(async request => { calls.push(request); return canvas; });
    const project = { id: "source-key", workspaceProjectId: "source-key", title: "绘图副本", nodes: [] };
    const drawingDocuments = [{ drawingId: "sketch", revision: "0", snapshot: { elements: [{ x: 10.399993896484375 }] } }];
    await bridge("put", "/canvas-projects/source-key", { project, drawingDocuments });
    await bridge("put", "/canvas-projects/source-key", { project, drawingDocuments });
    assert.deepEqual(calls[0], calls[1]);
    assert.deepEqual(calls[0].data.drawing_documents, drawingDocuments);
    assert.equal(calls[0].data.source_document, project);
    assert.equal("drawing_documents" in project, false);
});

test("历史恢复沿用打开列表时的绘图版本和墓碑，重试正文与键不变", async () => {
    const calls = [];
    const heads = { sketch: { revision: "9007199254740999", deleted: true } };
    const bridge = createHostCanvasContract(async request => {
        calls.push(request);
        return request.url.endsWith("/revisions") ? { items: [], row_version: canvas.row_version, drawing_heads: heads } : canvas;
    });
    const list = await bridge("get", "/canvas-projects/source-key/history");
    assert.deepEqual(list.drawingHeads, heads);
    const input = { revision: list.currentRevision, drawingHeads: list.drawingHeads };
    await bridge("post", "/canvas-projects/source-key/history/saved/restore", input);
    await bridge("post", "/canvas-projects/source-key/history/saved/restore", input);
    const writes = calls.filter(call => call.method === "post");
    assert.equal(writes.length, 2);
    assert.deepEqual(writes[0], writes[1]);
    assert.deepEqual(writes[0].data, { expected_row_version: canvas.row_version, expected_drawing_heads: heads });
});

test("首次创建固定原始请求并将资源回执交给本地日记", async () => {
    const calls = [];
    const resource_map = { "9007199254742001": "9007199254743001" };
    const resource_aliases = { "9007199254743001": ["9007199254742001"] };
    const bridge = createHostCanvasContract(async request => {
        calls.push(request);
        return { ...canvas, resource_map, resource_aliases };
    });
    const project = { id: "source-key", title: "完整副本", workspaceProjectId: "source-key", nodes: [{ metadata: { storageKey: "resource:9007199254742001" } }] };
    const result = await bridge("put", "/canvas-projects/source-key", { project });
    await bridge("put", "/canvas-projects/source-key", { project });
    assert.deepEqual(calls[0], calls[1]);
    assert.equal(calls[0].url, "/canvas-workspace");
    assert.equal(calls[0].data.source_document, project);
    assert.equal(calls[0].headers["Idempotency-Key"], "canvas-create:source-key");
    assert.deepEqual(result.project.resource_map, resource_map);
    assert.deepEqual(result.project.resource_aliases, resource_aliases);
    assert.equal(result.project.workspaceProjectId, canvas.project_id);
});

test("外观保存使用画布个人接口并保留字段比较合同", async () => {
    const calls = [];
    const input = { expected_preferences: { appearance: null }, preferences: { appearance: { mode: "light" } } };
    const bridge = createHostCanvasContract(async request => { calls.push(request); return canvas; });
    await bridge("put", "/canvas-projects/source-key/view-preferences", input);
    assert.equal(calls[1].url, `/projects/${canvas.project_id}/canvases/${canvas.id}/view-preferences`);
    assert.equal(calls[1].data, input);
});

test("源多画布复制用首张源键分组时解析同一个宿主项目", async () => {
    const calls = [];
    const bridge = createHostCanvasContract(async request => { calls.push(request); return canvas; });
    const project = { id: "second-copy", workspaceProjectId: "first-copy", title: "第二张" };
    await bridge("put", "/canvas-projects/second-copy", { project });
    assert.equal(calls[0].url, "/canvas-workspace/resolve/first-copy");
    assert.equal(calls[1].url, `/projects/${canvas.project_id}/canvases`);
    assert.equal(calls[1].data.source_document.workspaceProjectId, canvas.project_id);
    assert.equal(project.workspaceProjectId, "first-copy");
    assert.equal(calls[1].headers["Idempotency-Key"], "canvas-create:second-copy");
});

test("保存只转换服务契约，保留超大版本和原始文档，重复请求保留同一键", async () => {
    const calls = [];
    const bridge = createHostCanvasContract(async request => { calls.push(request); return canvas; });
    const document = { id: canvas.source_key, revision: canvas.row_version, nodes: [{ position: { x: -0.125, y: 12.375 } }] };
    const input = { opId: "operation-one", params: { canvasId: canvas.source_key, expectedRevision: canvas.row_version, document } };
    const result = await bridge("post", "/ops/canvas.document.commit", input);
    await bridge("post", "/ops/canvas.document.commit", input);
    assert.equal(result.revision, canvas.row_version);
    assert.deepEqual(calls[1], calls[3]);
    assert.equal(calls[1].data.source_document, document);
    assert.equal(calls[1].data.expected_row_version, canvas.row_version);
    assert.equal(calls[1].headers["Idempotency-Key"], "operation-one");
});

test("接口命名相同也不把源业务请求发给标准模式", async () => {
    const calls = [];
    const bridge = createHostCanvasContract(async request => { calls.push(request); return {}; });
    await bridge("post", "/projects", { title: "源项目" });
    await bridge("delete", "/assets/42");
    assert.deepEqual(calls.map(item => item.url), ["/canvas-runtime/projects", "/canvas-runtime/assets/42"]);
});

test("删除最后一个画布的响应丢失后从回执恢复，不重建也不再次删除", async () => {
    const calls = [];
    const bridge = createHostCanvasContract(async request => { calls.push(request); return { operation_kind: "canvas.archive" }; });
    assert.deepEqual(await bridge("delete", "/canvas-projects/source-key", { revision: canvas.row_version }), { id: "source-key" });
    assert.equal(calls.length, 1);
    await assert.rejects(bridge("delete", "/canvas-projects/source-key"), /版本/);
});

test("409 直接返回给原版草稿冲突流程，不强行重发", async () => {
    let calls = 0;
    const conflict = Object.assign(new Error("conflict"), { status: 409 });
    const bridge = createHostCanvasContract(async () => { if (++calls === 1) return canvas; throw conflict; });
    await assert.rejects(bridge("post", "/ops/canvas.document.commit", { opId: "one", params: { canvasId: "source-key", expectedRevision: "1", document: {} } }), error => error === conflict);
    assert.equal(calls, 2);
});

test("工作区分页与完整文档单次返回，列表不逐张读取图", async () => {
    const calls = [];
    const document = { id: "source-key", nodes: [{ id: "one", type: "text" }] };
    const bridge = createHostCanvasContract(async request => {
        calls.push(request);
        return { items: [{ ...canvas, canvas_title: "主画布", node_count: 1, preview_nodes: [], source_document: document }], page: 2, page_size: 50, total: 101, has_more: true };
    });
    const result = await bridge("get", "/canvas-projects", undefined, { page: 2, pageSize: 50, includeDocuments: true });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].params.include_documents, true);
    assert.equal(calls[0].params.page, 2);
    assert.equal(result.projects[0].document, document);
    assert.deepEqual(result.projects[0].previewNodes, document.nodes);
    assert.equal(result.hasMore, true);
});

test("私人资源来源在画布、历史与批量文档交给源修复流程之前恢复", async () => {
    const aliases = { "9007199254742001": ["9007199254741001"] };
    const observed = [];
    const document = { id: "source-key", nodes: [] };
    const bridge = createHostCanvasContract(async request => {
        if (request.url.includes("/resolve/")) return canvas;
        if (request.url === "/canvas-workspace") return { items: [{ ...canvas, node_count: 0, preview_nodes: [], source_document: document, resource_aliases: aliases }], page: 1, page_size: 50, total: 1, has_more: false };
        return { ...canvas, source_document: document, resource_aliases: aliases, revision: { id: "history", row_version: "1" } };
    }, value => { observed.push(value); });
    const current = await bridge("get", "/canvas-projects/source-key");
    assert.equal(current.project, document);
    assert.deepEqual(observed, [aliases]);
    await bridge("get", "/canvas-projects/source-key/history/history");
    await bridge("get", "/canvas-projects", undefined, { includeDocuments: true });
    assert.deepEqual(observed, [aliases, aliases, aliases]);
});

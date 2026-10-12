import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setImmediate } from "node:timers/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

function definitions(file, names) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.ES2022, true);
    return parsed.statements.filter(statement => names.includes(statement.name?.text))
        .map(statement => statement.getText(parsed).replace(/^export /, "")).join("\n");
}
const submissionCode = ts.transpileModule([
    definitions("../src/services/host-generation-admission.ts", ["canvasTaskAdmissionIdentity"]),
    definitions("../src/services/storyboard-submission.ts", ["submitStoryboardTask"]),
].join("\n"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

function setup({ confirmed = true, changeAfterApproval = false, saveFails = false } = {}) {
    const calls = [];
    let changed = false;
    const request = { projectId: "canvas", logicalModelId: "17", model: "saved", prompt: "剧情", input: { metadata: { nodeId: "script", sourceNodeId: "script", clientOperationId: "storyboard:stable" } } };
    const api = {
        create: async input => { calls.push(["create", input]); return { run: { id: "19", executionEpoch: "0" } }; },
        claim: async () => { calls.push(["claim"]); return { id: "19", executionEpoch: "1", executionOwner: "worker" }; },
        prepare: async (_id, input) => { calls.push(["prepare", input]); return { id: "23", execution: { model: "saved" } }; },
        approve: async () => { calls.push(["approve"]); changed = changeAfterApproval; return { submissions: [{ id: "23", approvedAt: "approved" }] }; },
        execute: async () => { calls.push(["execute"]); return { id: "29" }; },
        release: async () => { calls.push(["release"]); },
    };
    const context = vm.createContext({
        creationRuns: api, nanoid: () => "owner", captureUserScope: () => ({ userScope: "31", epoch: 1 }),
        persistCanvasGenerationSource: async input => { calls.push(["save", input]); if (saveFails) throw new Error("save failed"); },
        setInterval: () => 1, clearInterval: () => undefined,
    });
    vm.runInContext(`${submissionCode}\nthis.submit = submitStoryboardTask;`, context);
    const options = {
        signal: new AbortController().signal,
        assertCurrent: () => { if (changed) throw new Error("rows changed"); },
        confirm: async () => { calls.push(["confirm"]); return confirmed; },
    };
    return { calls, request, run: () => context.submit(request, options, api) };
}

test("storyboard saves source before preparation and only executes an approved request", async () => {
    const state = setup();
    assert.equal((await state.run()).id, "29");
    await setImmediate();
    assert.deepEqual(state.calls.map(call => call[0]), ["save", "create", "claim", "prepare", "confirm", "approve", "execute", "release"]);
    const created = state.calls.find(call => call[0] === "create")[1];
    assert.equal(created.clientKey, "storyboard:stable");
    assert.equal(created.state.nodeId, "script");
    assert.equal(created.state.logicalModelId, "17");
    assert.equal(state.calls.find(call => call[0] === "prepare")[1].request, state.request);
});

test("cancelled storyboard preparation releases its lease without approval or execution", async () => {
    const state = setup({ confirmed: false });
    assert.equal(await state.run(), undefined);
    await setImmediate();
    assert.deepEqual(state.calls.map(call => call[0]), ["save", "create", "claim", "prepare", "confirm", "release"]);
});

test("source edits after approval stop execution and release the original lease", async () => {
    const state = setup({ changeAfterApproval: true });
    await assert.rejects(state.run(), /rows changed/);
    await setImmediate();
    assert.equal(state.calls.some(call => call[0] === "execute"), false);
    assert.equal(state.calls.at(-1)[0], "release");
});

test("failed source saving never creates a run", async () => {
    const state = setup({ saveFails: true });
    await assert.rejects(state.run(), /save failed/);
    assert.deepEqual(state.calls.map(call => call[0]), ["save"]);
});

test("storyboard parsing preserves stable task-derived IDs on repeated recovery", () => {
    const code = ts.transpileModule(definitions("../src/lib/canvas/canvas-project-domain.ts", ["createStoryboardRow", "parseStoryboardRowsPayload", "storyboardRowsFromTask"]), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
    const context = vm.createContext({ normalizeStoryboardAssetBindings: values => values || [] });
    vm.runInContext(`${code}\nthis.parse = storyboardRowsFromTask;`, context);
    const task = { id: "18446744073709551614", operation: "storyboard", resultJson: JSON.stringify({ title: "车站", rows: [{ id: "shot:18446744073709551614:1", durationSeconds: 5, characters: [{ characterName: "旅客" }], plotDescription: "道别。" }] }) };
    assert.equal(JSON.stringify(context.parse(task)), JSON.stringify(context.parse(task)));
    assert.equal(context.parse(task).rows[0].id, "shot:18446744073709551614:1");
    assert.equal(context.parse(task).rows[0].characters[0].characterName, "旅客");
});

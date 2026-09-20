import { Button } from "antd";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type {
  AssetKind,
  AssetVisualRef,
  GlobalAsset,
} from "../../../features/assets/asset-model";
import {
  sampleAssets,
  sampleImage,
} from "../../../features/projects/episode-demo";
import { ImagePreview } from "./ImagePreview";
import {
  addAsset,
  editAsset,
  assetText,
  removeAsset,
  type AssetItem,
  type EpisodeWorkflow,
  type MediaRef,
} from "../../../features/projects/episode-workflow";
import {
  importProjectMedia,
  listUsableMedia,
  type ListedMedia,
} from "../../../features/projects/episode-media";
import {
  readProjectResources,
  saveProjectResources,
} from "../../../features/projects/project-detail-model";

type ShareChoice = "link" | "create";
type ShareInput = AssetItem;
export type ShareResult =
  | { ok: true; linkedResourceId: string; resources: GlobalAsset[] }
  | { ok: false; reason: "invalid" }
  | { ok: false; reason: "choice_required"; matches: GlobalAsset[] };

const kindLabel: Record<AssetKind, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
};
const assetKinds = ["character", "prop", "scene"] as const;
const addLabel: Record<AssetKind, string> = {
  character: "新增人物",
  prop: "新增道具",
  scene: "新增场景",
};

export function shareAsset(
  asset: ShareInput,
  resources: GlobalAsset[],
  choice?: ShareChoice,
  visualRef?: AssetVisualRef,
  mediaItems: readonly ListedMedia[] = [],
): ShareResult {
  const selected = selectedRef(asset);
  if (
    !asset.name.trim() ||
    !asset.description.trim() ||
    asset.review !== "confirmed" ||
    !selected ||
    !["demo-image", "project-image"].includes(selected.kind) ||
    (selected.kind === "project-image" &&
      !canBindImportedImage(mediaItems, selected.id))
  )
    return { ok: false, reason: "invalid" };
  visualRef ??= {
    kind: selected.kind as AssetVisualRef["kind"],
    id: selected.id,
  };
  const linked = resources.find((item) => item.id === asset.linkedResourceId);
  const matches = resources.filter(
    (item) =>
      item.kind === asset.kind &&
      item.name.trim().toLocaleLowerCase() ===
        asset.name.trim().toLocaleLowerCase(),
  );
  if (!choice && !linked && matches.length)
    return { ok: false, reason: "choice_required", matches };
  const target =
    choice === "create"
      ? undefined
      : (linked ?? (choice === "link" ? matches[0] : undefined));
  if (target) {
    const next = {
      ...target,
      name: asset.name.trim(),
      description: asset.description.trim(),
      ...(visualRef ? { visualRef } : {}),
    };
    return {
      ok: true,
      linkedResourceId: target.id,
      resources: resources.map((item) => (item.id === target.id ? next : item)),
    };
  }
  const created: GlobalAsset = {
    id: crypto.randomUUID(),
    kind: asset.kind,
    name: asset.name.trim(),
    description: asset.description.trim(),
    category: "",
    tags: [],
    createdAt: new Date().toISOString(),
    ...(visualRef ? { visualRef } : {}),
  };
  return {
    ok: true,
    linkedResourceId: created.id,
    resources: [...resources, created],
  };
}

function selectedRef(asset: AssetItem): MediaRef | null {
  return (
    asset.imageCandidates.find((item) => item.id === asset.selectedImageId)
      ?.value ?? null
  );
}

export function canBindImportedImage(
  items: readonly { id: string; mime: string; availability: string }[],
  mediaId: string,
) {
  return items.some(
    (item) =>
      item.id === mediaId &&
      item.availability === "available" &&
      item.mime.startsWith("image/"),
  );
}

function addAssetImageCandidate(
  value: EpisodeWorkflow,
  assetId: string,
  candidate: AssetItem["imageCandidates"][number],
): EpisodeWorkflow {
  const stale = editAsset(value, assetId, {});
  return {
    ...stale,
    assets: stale.assets.map((item) =>
      item.id === assetId
        ? { ...item, imageCandidates: [...item.imageCandidates, candidate] }
        : item,
    ),
  };
}

/** Adoption records the candidate itself; a demo never becomes project media. */
export function adoptAssetImage(
  value: EpisodeWorkflow,
  assetId: string,
  candidateId: string,
): EpisodeWorkflow {
  const asset = value.assets.find((item) => item.id === assetId);
  if (!asset?.imageCandidates.some((item) => item.id === candidateId))
    return value;
  const stale = editAsset(value, assetId, {});
  return {
    ...stale,
    assets: stale.assets.map((item) =>
      item.id === assetId
        ? { ...item, selectedImageId: candidateId, review: "review" }
        : item,
    ),
  };
}

export function AssetsStage({
  value,
  readOnly,
  ready,
  projectId,
  onChange,
  onApply,
}: {
  value: EpisodeWorkflow;
  readOnly: boolean;
  ready: boolean;
  projectId: string;
  onChange: (next: EpisodeWorkflow) => void;
  onApply?: (change: (current: EpisodeWorkflow) => EpisodeWorkflow) => void;
}) {
  const latest = useRef(value);
  latest.current = value;
  const lifetime = useRef({ active: true });
  useEffect(() => {
    const token = { active: true };
    lifetime.current = token;
    return () => {
      token.active = false;
    };
  }, [projectId, readOnly, ready]);
  const [resources, setResources] = useState(() =>
    readProjectResources(projectId),
  );
  const [usableImages, setUsableImages] = useState<
    Awaited<ReturnType<typeof listUsableMedia>>
  >([]);
  const [message, setMessage] = useState(
    ready ? "" : "本地媒体服务尚未就绪，无法读取或导入项目图片。 ",
  );
  const [choiceFor, setChoiceFor] = useState<string | null>(null);
  const [importing, setImporting] = useState<string | null>(null);
  const [activeKind, setActiveKind] = useState<AssetKind>("character");
  const [editor, setEditor] = useState<{ kind: AssetKind; id?: string } | null>(
    null,
  );
  const [draft, setDraft] = useState({ name: "", description: "" });
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    let active = true;
    setResources(readProjectResources(projectId));
    setChoiceFor(null);
    setEditor(null);
    if (!ready) return setUsableImages([]);
    void listUsableMedia(projectId, "image/").then((items) => {
      if (active) setUsableImages(items);
    });
    return () => {
      active = false;
    };
  }, [projectId, ready]);

  useEffect(() => {
    if (!editor) return;
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, [editor?.id, editor?.kind]);

  const activeAssets = value.assets.filter(
    (asset) => asset.kind === activeKind,
  );
  const sample = activeAssets.length
    ? null
    : sampleAssets().find((asset) => asset.kind === activeKind);
  const editingAsset = editor?.id
    ? value.assets.find((asset) => asset.id === editor.id)
    : null;
  const hasUnsavedText =
    !!editingAsset &&
    (draft.name !== editingAsset.name ||
      draft.description !== editingAsset.description);
  function openEditor(kind: AssetKind, asset?: AssetItem) {
    if (readOnly) return;
    setMessage(ready ? "" : "本地媒体服务尚未就绪，无法导入项目图片。");
    setDraft({
      name: asset?.name ?? "",
      description: asset?.description ?? "",
    });
    setChoiceFor(null);
    setEditor({ kind, ...(asset ? { id: asset.id } : {}) });
  }
  function closeEditor() {
    setEditor(null);
    setChoiceFor(null);
  }
  function saveEditor() {
    if (!editor || readOnly || !draft.name.trim() || !draft.description.trim())
      return;
    if (editor.id && !editingAsset) return;
    const text = {
      name: draft.name.trim(),
      description: draft.description.trim(),
    };
    if (editingAsset) {
      if (
        text.name !== editingAsset.name ||
        text.description !== editingAsset.description
      )
        update(editAsset(value, editingAsset.id, text));
    } else {
      const next = addAsset(value, editor.kind);
      update(editAsset(next, next.assets[next.assets.length - 1].id, text));
    }
    closeEditor();
  }
  function selectTabByKey(
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) {
    let nextIndex: number;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % assetKinds.length;
    else if (event.key === "ArrowLeft")
      nextIndex = (index - 1 + assetKinds.length) % assetKinds.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = assetKinds.length - 1;
    else return;
    event.preventDefault();
    setActiveKind(assetKinds[nextIndex]);
    event.currentTarget.parentElement
      ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
      [nextIndex]?.focus();
  }
  function update(next: EpisodeWorkflow) {
    onChange(next);
  }
  function select(asset: AssetItem, candidateId: string) {
    update(adoptAssetImage(value, asset.id, candidateId));
  }
  function confirm(asset: AssetItem) {
    if (!canConfirm(asset)) return;
    const nextAssets = value.assets.map((item) =>
      item.id === asset.id
        ? {
            ...item,
            approvedText: assetText(item),
            review: "confirmed" as const,
          }
        : item,
    );
    update({
      ...value,
      assets: nextAssets,
      reviews: {
        ...value.reviews,
        assets: nextAssets.every((item) => item.review === "confirmed")
          ? "confirmed"
          : "review",
      },
    });
  }
  function share(asset: AssetItem, choice?: ShareChoice) {
    if (readOnly || !canConfirm(asset) || asset.review !== "confirmed") return;
    const chosen = selectedRef(asset);
    const visualRef: AssetVisualRef | undefined =
      chosen?.kind === "project-image" || chosen?.kind === "demo-image"
        ? { kind: chosen.kind, id: chosen.id }
        : undefined;
    const result = shareAsset(
      asset,
      resources,
      choice,
      visualRef,
      usableImages,
    );
    if (!result.ok) {
      if (result.reason === "choice_required") setChoiceFor(asset.id);
      else setMessage("请先补全素材名称和描述，选图并确认素材可用。");
      return;
    }
    try {
      saveProjectResources(projectId, result.resources);
    } catch {
      setMessage("资源库未能保存，请检查本地空间后重试；原关联已保留。");
      return;
    }
    setResources(result.resources);
    setChoiceFor(null);
    update({
      ...value,
      assets: value.assets.map((item) =>
        item.id === asset.id
          ? { ...item, linkedResourceId: result.linkedResourceId }
          : item,
      ),
    });
    setMessage("已更新项目资源库。 ");
  }
  async function importImage(asset: AssetItem) {
    if (readOnly || !ready) return;
    const token = lifetime.current;
    setImporting(asset.id);
    const result = await importProjectMedia(projectId, "reference");
    if (!token.active) return;
    setImporting(null);
    if (!result.ok) {
      setMessage(
        result.reason === "cancelled"
          ? "未选择图片。"
          : result.reason === "uncertain"
            ? "导入结果仍待核对；没有再次提交导入。"
            : (result.message ?? "图片导入失败。 "),
      );
      return;
    }
    const latestImages = await listUsableMedia(projectId, "image/");
    if (!token.active) return;
    setUsableImages(latestImages);
    if (!canBindImportedImage(latestImages, result.mediaId)) {
      setMessage(
        "导入结果不是可用图片，未添加为候选。请在项目媒体库核对导入或重新定位原文件。 ",
      );
      return;
    }
    const candidate = {
      id: crypto.randomUUID(),
      source: "import" as const,
      value: { kind: "project-image" as const, id: result.mediaId },
      usableForVideo: true,
    };
    const apply = (current: EpisodeWorkflow) =>
      current.assets.some((item) => item.id === asset.id)
        ? adoptAssetImage(
            addAssetImageCandidate(current, asset.id, candidate),
            asset.id,
            candidate.id,
          )
        : current;
    if (onApply) onApply(apply);
    else {
      latest.current = apply(latest.current);
      update(latest.current);
    }
    setMessage("图片已导入，等待你确认采用。 ");
  }
  function canConfirm(asset: AssetItem) {
    const ref = selectedRef(asset);
    return (
      !!asset.name.trim() &&
      !!asset.description.trim() &&
      !!ref &&
      (ref.kind === "demo-image" ||
        (ref.kind === "project-image" &&
          canBindImportedImage(usableImages, ref.id)))
    );
  }
  function addExisting(asset: AssetItem, mediaId: string) {
    if (
      asset.imageCandidates.some(
        (candidate) =>
          candidate.value.kind === "project-image" &&
          candidate.value.id === mediaId,
      )
    )
      return;
    const candidate = {
      id: crypto.randomUUID(),
      source: "import" as const,
      value: { kind: "project-image" as const, id: mediaId },
      usableForVideo: true,
    };
    update(addAssetImageCandidate(value, asset.id, candidate));
  }

  return (
    <>
      <div className="episode-stage-heading">
        <div>
          <h2>素材图片</h2>
          <p>查看角色、道具和场景的提示词与参考图片。</p>
        </div>
      </div>
      {message && !editor && (
        <p className="episode-help" role="status">
          {message}
        </p>
      )}
      <div className="episode-asset-tabs" role="tablist" aria-label="素材分类">
        {assetKinds.map((kind, index) => (
          <button
            key={kind}
            id={"episode-assets-tab-" + kind}
            type="button"
            role="tab"
            aria-selected={activeKind === kind}
            aria-controls="episode-assets-panel"
            tabIndex={activeKind === kind ? 0 : -1}
            onClick={() => setActiveKind(kind)}
            onKeyDown={(event) => selectTabByKey(event, index)}
          >
            {kindLabel[kind]}
          </button>
        ))}
      </div>
      <section
        id="episode-assets-panel"
        className="episode-asset-panel"
        role="tabpanel"
        aria-labelledby={"episode-assets-tab-" + activeKind}
        tabIndex={0}
      >
        <div className="episode-library-grid">
          {(sample ? [sample] : activeAssets).map((asset) => {
            const isSample = !!sample;
            return (
              <article key={asset.id} className="episode-library-asset-card">
                <div className="episode-library-card-copy">
                  <h3>{asset.name || "未命名" + kindLabel[asset.kind]}</h3>
                  <p className="episode-library-description">
                    {asset.description || "暂无提示词描述"}
                  </p>
                  {isSample ? (
                    <span className="episode-library-sample-note">
                      演示内容，未保存到本集
                    </span>
                  ) : (
                    <Button
                      type="primary"
                      disabled={readOnly}
                      onClick={() => openEditor(asset.kind, asset)}
                    >
                      编辑
                    </Button>
                  )}
                </div>
                <div className="episode-library-cover">
                  {isSample ? (
                    <span className="episode-image-empty">暂无图片</span>
                  ) : (
                    <ImagePreview
                      media={selectedRef(asset)}
                      label={asset.name + "已采用图片"}
                      projectId={projectId}
                      mediaItems={usableImages}
                      kind={asset.kind}
                    />
                  )}
                </div>
              </article>
            );
          })}
          <div className="episode-library-add-card">
            <span className="episode-library-add-icon" aria-hidden="true" />
            <Button
              type="primary"
              disabled={readOnly}
              onClick={() => openEditor(activeKind)}
            >
              {addLabel[activeKind]}
            </Button>
          </div>
        </div>
      </section>
      {editor && (
        <dialog
          ref={dialogRef}
          className="ui-dialog episode-asset-dialog"
          aria-labelledby="episode-asset-dialog-title"
          onClose={(event) => {
            if (!event.currentTarget.open) closeEditor();
          }}
        >
          <header>
            <h2 id="episode-asset-dialog-title">
              {editingAsset
                ? "编辑" + kindLabel[editor.kind]
                : addLabel[editor.kind]}
            </h2>
            <Button
              type="primary"
              className="episode-asset-dialog-close"
              aria-label="关闭"
              onClick={() => dialogRef.current?.close()}
            >
              关闭
            </Button>
          </header>
          <div className="episode-asset-dialog-body">
            <form
              id="episode-asset-edit-form"
              className="episode-asset-edit-form"
              onSubmit={(event) => {
                event.preventDefault();
                saveEditor();
              }}
            >
              <label>
                名称
                <input
                  autoFocus
                  required
                  value={draft.name}
                  onChange={(event) =>
                    setDraft({ ...draft, name: event.target.value })
                  }
                />
              </label>
              <label>
                提示词描述
                <textarea
                  required
                  rows={5}
                  value={draft.description}
                  onChange={(event) =>
                    setDraft({ ...draft, description: event.target.value })
                  }
                />
              </label>
            </form>
            {editingAsset && (
              <section
                className="episode-asset-image-editor"
                aria-label="参考图片"
              >
                <h3>参考图片</h3>
                <div className="episode-asset-preview">
                  <ImagePreview
                    media={selectedRef(editingAsset)}
                    label={editingAsset.name + "已采用图片"}
                    projectId={projectId}
                    mediaItems={usableImages}
                    kind={editingAsset.kind}
                  />
                </div>
                <div className="episode-asset-actions">
                  <Button
                    type="primary"
                    disabled={readOnly}
                    onClick={() => {
                      const ref = sampleImage(
                        editingAsset.id +
                          "-" +
                          editingAsset.imageCandidates.length,
                      );
                      const candidate = {
                        id: crypto.randomUUID(),
                        source: "demo" as const,
                        value: ref,
                        usableForVideo: true,
                      };
                      update(
                        addAssetImageCandidate(
                          value,
                          editingAsset.id,
                          candidate,
                        ),
                      );
                    }}
                  >
                    生成演示图片
                  </Button>
                  <Button
                    type="primary"
                    disabled={
                      readOnly || !ready || importing === editingAsset.id
                    }
                    onClick={() => void importImage(editingAsset)}
                  >
                    {importing === editingAsset.id
                      ? "正在导入…"
                      : "导入项目图片"}
                  </Button>
                </div>
                {ready && (
                  <label className="episode-asset-media-select">
                    选择已可用项目图片
                    <select
                      value=""
                      disabled={readOnly}
                      onChange={(event) => {
                        if (event.target.value)
                          addExisting(editingAsset, event.target.value);
                      }}
                    >
                      <option value="">从项目媒体库添加</option>
                      {usableImages.map((image) => (
                        <option key={image.id} value={image.id}>
                          {image.id.slice(0, 8)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {editingAsset.imageCandidates.length > 0 && (
                  <div
                    className="episode-image-candidates"
                    aria-label={editingAsset.name + "图片候选"}
                  >
                    {editingAsset.imageCandidates.map((candidate) => (
                      <button
                        type="button"
                        key={candidate.id}
                        className={
                          candidate.id === editingAsset.selectedImageId
                            ? "selected"
                            : ""
                        }
                        disabled={readOnly}
                        onClick={() => select(editingAsset, candidate.id)}
                      >
                        <ImagePreview
                          media={candidate.value}
                          label={editingAsset.name + "候选"}
                          projectId={projectId}
                          mediaItems={usableImages}
                          kind={editingAsset.kind}
                        />
                        {candidate.value.kind === "demo-image"
                          ? "演示图片"
                          : candidate.value.kind === "project-image"
                            ? "项目图片"
                            : "不可用于素材图片"}
                      </button>
                    ))}
                  </div>
                )}
                <div className="episode-asset-actions">
                  <Button
                    type="primary"
                    disabled={
                      readOnly || hasUnsavedText || !canConfirm(editingAsset)
                    }
                    onClick={() => confirm(editingAsset)}
                  >
                    确认采用
                  </Button>
                  <Button
                    type="primary"
                    disabled={
                      readOnly ||
                      hasUnsavedText ||
                      editingAsset.review !== "confirmed" ||
                      !canConfirm(editingAsset)
                    }
                    onClick={() => share(editingAsset)}
                  >
                    共享到项目资源库
                  </Button>
                </div>
                {choiceFor === editingAsset.id && (
                  <div
                    className="episode-share-choice"
                    role="group"
                    aria-label="重复素材处理"
                  >
                    <p>
                      项目资源库中已有同名{kindLabel[editingAsset.kind]}
                      。请选择链接已有资源或另建资源。
                    </p>
                    <Button
                      type="primary"
                      onClick={() => share(editingAsset, "link")}
                    >
                      链接已有资源
                    </Button>
                    <Button
                      type="primary"
                      onClick={() => share(editingAsset, "create")}
                    >
                      另建资源
                    </Button>
                  </div>
                )}
                {message && (
                  <p className="episode-help" role="status">
                    {message}
                  </p>
                )}
                <p className="episode-asset-save-note">
                  图片操作会立即保存；名称与提示词请点击“保存”。
                </p>
              </section>
            )}
          </div>
          <footer className="episode-asset-dialog-actions">
            {editingAsset && (
              <Button
                type="primary"
                className="episode-asset-delete"
                danger
                disabled={readOnly}
                onClick={() => {
                  if (!window.confirm("确定删除这项素材吗？")) return;
                  update(removeAsset(value, editingAsset.id));
                  closeEditor();
                }}
              >
                删除素材
              </Button>
            )}
            <Button type="primary" onClick={() => dialogRef.current?.close()}>
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              form="episode-asset-edit-form"
              disabled={!draft.name.trim() || !draft.description.trim()}
            >
              保存
            </Button>
          </footer>
        </dialog>
      )}
    </>
  );
}

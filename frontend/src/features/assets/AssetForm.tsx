import { useState, type FormEvent } from "react";
import { createAsset, type AssetKind, type GlobalAsset } from "./asset-model";
import { Dialog } from "../../components/ui/Dialog";
import { Button, Input } from "antd";

const names = { character: "角色", scene: "场景", prop: "道具" };
export function AssetForm({
  kind,
  onSave,
  onClose,
}: {
  kind: AssetKind;
  onSave: (asset: GlobalAsset) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  const [tags, setTags] = useState("");
  const [time, setTime] = useState("");
  const [error, setError] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    try {
      onSave(
        createAsset(kind, {
          name,
          description:
            kind === "scene" && time.trim()
              ? `${time.trim()} · ${description.trim()}`
              : description,
          category,
          tags,
        }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "请检查输入内容");
    }
  }
  return (
    <Dialog title={`新建${names[kind]}`} onClose={onClose}>
      <form className="studio-form" onSubmit={submit}>
        <label htmlFor="asset-name">
          {kind === "scene" ? "地点" : "名称"}
          <Input
            id="asset-name"
            autoFocus
            required
            maxLength={120}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={
              kind === "scene" ? "例如：雨夜街口" : `输入${names[kind]}名称`
            }
          />
        </label>
        {kind === "scene" && (
          <label htmlFor="asset-time">
            时间
            <Input
              id="asset-time"
              value={time}
              maxLength={60}
              onChange={(e) => setTime(e.target.value)}
              placeholder="例如：夜晚"
            />
          </label>
        )}
        <label htmlFor="asset-category">
          分类
          <Input
            id="asset-category"
            value={category}
            maxLength={60}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="选填"
          />
        </label>
        <label htmlFor="asset-description">
          描述
          <Input.TextArea
            id="asset-description"
            rows={3}
            value={description}
            maxLength={500}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={`描述${names[kind]}的外观或用途`}
          />
        </label>
        <label htmlFor="asset-tags">
          标签
          <Input
            id="asset-tags"
            value={tags}
            maxLength={200}
            onChange={(e) => setTags(e.target.value)}
            placeholder="用逗号分隔，选填"
          />
        </label>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" htmlType="submit">
            创建素材
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

import { useEffect, useState, type FormEvent } from "react";
import type { ProjectSession } from "../../types/projects";
import { Button, Input, Select } from "antd";
import { Dialog } from "../../components/ui/Dialog";
import {
  ensureExampleEpisode,
  readEpisodes,
  readProjectDetails,
  saveEpisodes,
  saveProjectDetails,
  type Episode,
  type ProjectDetails,
} from "./project-detail-model";
import { ProjectResourceLibrary } from "./ProjectResourceLibrary";

export function ProjectOverview({
  session,
  onOpenEpisode,
}: {
  session: ProjectSession;
  onOpenEpisode: (episode: Episode) => void;
}) {
  const [episodes, setEpisodes] = useState(() =>
    readEpisodes(session.projectId),
  );
  const [details, setDetails] = useState(() =>
    readProjectDetails(session.projectId),
  );
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [synopsis, setSynopsis] = useState("");

  useEffect(() => {
    setEpisodes(ensureExampleEpisode(session.projectId));
    setDetails(readProjectDetails(session.projectId));
  }, [session.projectId]);

  function updateDetails(patch: Partial<ProjectDetails>) {
    setDetails((current) => {
      const next = { ...current, ...patch };
      saveProjectDetails(session.projectId, next);
      return next;
    });
  }

  function addEpisode(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    const next = [
      ...episodes,
      {
        id: crypto.randomUUID(),
        title: title.trim(),
        synopsis: synopsis.trim(),
      },
    ];
    setEpisodes(next);
    saveEpisodes(session.projectId, next);
    setTitle("");
    setSynopsis("");
    setCreating(false);
  }

  return (
    <div className="project-overview">
      <section
        className="overview-card overview-info"
        aria-labelledby="project-info-title"
      >
        <div className="overview-heading">
          <h2 id="project-info-title">剧集信息</h2>
        </div>
        <div className="overview-top-row">
          <h3>{session.project.name}</h3>
          <label data-project-style htmlFor="project-style">
            图片 / 视频风格
            <Input
              id="project-style"
              value={details.style}
              maxLength={100}
              placeholder="例如：清透水彩、都市写实"
              onChange={(event) => updateDetails({ style: event.target.value })}
            />
          </label>
          <label
            className="overview-aspect"
            data-project-aspect
            htmlFor="project-aspect"
          >
            画面比例
            <Select
              id="project-aspect"
              aria-label="画面比例"
              value={details.aspect ?? session.project.aspect}
              onChange={(aspect: "16:9" | "9:16") => updateDetails({ aspect })}
              options={[
                { value: "16:9", label: "横屏 16:9" },
                { value: "9:16", label: "竖屏 9:16" },
              ]}
            />
            <small>仅在当前工作台显示，不影响实际导出画幅</small>
          </label>
        </div>
        <label
          className="overview-synopsis"
          data-project-synopsis
          htmlFor="project-synopsis"
        >
          故事梗概
          <Input.TextArea
            id="project-synopsis"
            value={details.synopsis}
            maxLength={2000}
            rows={3}
            placeholder="简要描述这部短剧的主线故事"
            onChange={(event) =>
              updateDetails({ synopsis: event.target.value })
            }
          />
        </label>
      </section>

      <section className="overview-card" aria-labelledby="episodes-title">
        <div className="overview-heading">
          <h2 id="episodes-title">
            分集列表 <small>共 {episodes.length} 集</small>
          </h2>
          <Button type="primary" onClick={() => setCreating(true)}>
            新增一集
          </Button>
        </div>
        {episodes.length ? (
          <div className="episode-grid">
            {episodes.map((episode, index) => (
              <button
                type="button"
                className="episode-card"
                key={episode.id}
                onClick={() => onOpenEpisode(episode)}
                aria-label={`进入第 ${index + 1} 集：${episode.title}`}
              >
                <span className="episode-number">第 {index + 1} 集</span>
                <strong>{episode.title}</strong>
                <span className="episode-synopsis">
                  {episode.synopsis || "尚未填写本集概要"}
                </span>
                <span className="episode-enter">
                  进入制作 <span aria-hidden="true">→</span>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <p className="overview-empty">
            还没有分集。新增一集后，点击卡片进入制作流程。
          </p>
        )}
      </section>

      <ProjectResourceLibrary projectId={session.projectId} />

      {creating && (
        <Dialog title="新增一集" onClose={() => setCreating(false)}>
          <form className="studio-form" onSubmit={addEpisode}>
            <label>
              标题
              <input
                autoFocus
                required
                maxLength={120}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="例如：借光的人"
              />
            </label>
            <label>
              内容概要
              <textarea
                rows={3}
                maxLength={500}
                value={synopsis}
                onChange={(event) => setSynopsis(event.target.value)}
                placeholder="简要描述这一集"
              />
            </label>
            <div className="dialog-actions">
              <Button onClick={() => setCreating(false)}>取消</Button>
              <Button htmlType="submit" type="primary">
                添加分集
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </div>
  );
}

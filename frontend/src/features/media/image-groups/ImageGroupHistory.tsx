import { errorMessage } from '@host/api/http';
import { imageGroups } from '@host/api/media/image-groups';
import { ImageGroupPreview } from '@host/features/media/image-groups/ImageGroupPreview';
import type { ImageGroup, ImageTargetKind } from '@host/types/media/image-groups';
import { Alert, Button, Spin } from 'antd';
import { useEffect, useRef, useState } from 'react';
export function ImageGroupHistory({
  kind,
  targetId,
  disabled,
  onBeforeChange,
  onChanged,
}: {
  kind: ImageTargetKind;
  targetId: string;
  disabled: boolean;
  onBeforeChange: () => Promise<boolean>;
  onChanged: () => void;
}) {
  const [groups, setGroups] = useState<ImageGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const moreRequest = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    moreRequest.current?.abort();
    setLoading(true);
    setGroups([]);
    void imageGroups
      .history(kind, targetId, 0, controller.signal)
      .then((page) => {
        if (!controller.signal.aborted) {
          setGroups(page.items);
          setTotal(page.total);
          setError('');
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      moreRequest.current?.abort();
    };
  }, [kind, targetId, revision]);
  async function more() {
    if (loading) return;
    const controller = new AbortController();
    moreRequest.current?.abort();
    moreRequest.current = controller;
    setLoading(true);
    try {
      const page = await imageGroups.history(kind, targetId, groups.length, controller.signal);
      if (!controller.signal.aborted) {
        setGroups((current) => [
          ...current,
          ...page.items.filter((item) => !current.some((prior) => prior.id === item.id)),
        ]);
        setTotal(page.total);
        setError('');
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  const refresh = () => setRevision((value) => value + 1);
  return (
    <section className="image-group-history">
      <div className="image-group-history-heading">
        <h4>图片历史</h4>
        <Button size="small" loading={loading} onClick={refresh}>
          刷新图片历史
        </Button>
      </div>
      {error && <Alert type="error" message={error} action={<Button onClick={refresh}>重试</Button>} />}
      {loading && !groups.length ? (
        <div role="status">
          <Spin />
          <span>正在读取图片历史…</span>
        </div>
      ) : !groups.length && !error ? (
        <p className="creation-help">生成或处理过的图片组会保留在这里。</p>
      ) : (
        groups.map((group) => (
          <section className="image-history-group" key={group.id}>
            <div className="image-history-group-title">
              <time>
                {new Date(
                  `${group.created_at.endsWith('Z') ? group.created_at : `${group.created_at}Z`}`,
                ).toLocaleString('zh-CN')}
              </time>
              <span>
                {group.is_current
                  ? '当前图片组'
                  : group.kind === 'split'
                    ? '宫格切分'
                    : group.kind === 'edit'
                      ? '图片处理'
                      : '历史图片组'}
              </span>
            </div>
            <ImageGroupPreview
              group={group}
              label="历史图片"
              historical
              disabled={disabled}
              onBeforeChange={onBeforeChange}
              onRefresh={refresh}
              onChanged={onChanged}
            />
          </section>
        ))
      )}
      {groups.length < total && (
        <Button loading={loading} onClick={() => void more()}>
          加载更多图片组
        </Button>
      )}
    </section>
  );
}

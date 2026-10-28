import { mediaLibrary } from '@host/api/media/library';
import { generationError } from '@host/features/generations/presentation/presentation';
import { isServerId } from '@host/features/shared/async/request-receipt';
import type { MediaAsset } from '@host/types/generations/api';
import { Button, Image, Modal, Spin } from 'antd';
import { useEffect, useRef, useState } from 'react';
export function AssetPreview({ id, onClose }: { id: string; onClose: () => void }) {
  const [asset, setAsset] = useState<MediaAsset | null>(null);
  const [error, setError] = useState('');
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  const player = useRef<HTMLMediaElement | null>(null);
  const trigger = useRef(document.activeElement);
  useEffect(() => {
    return () => {
      if (trigger.current instanceof HTMLElement && trigger.current.isConnected)
        trigger.current.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setAsset(null);
    setError('');
    setFailed(false);
    if (!isServerId(id)) {
      setError('无法找到该资产，请返回资产库重新选择。');
      return;
    }
    void mediaLibrary
      .detail(id, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setAsset(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(generationError(cause));
      });
    return () => controller.abort();
  }, [id, revision]);
  useEffect(() => {
    const media = player.current;
    return () => {
      if (media) {
        media.pause();
        media.removeAttribute('src');
        media.load();
      }
    };
  }, [asset?.url, failed]);

  if (asset?.media_type === 'image' && asset.url && !failed)
    return (
      <Image
        src={asset.url}
        alt={asset.name}
        rootClassName="media-asset-image-preview"
        style={{ display: 'none' }}
        onError={() => setFailed(true)}
        preview={{
          visible: true,
          movable: true,
          minScale: 0.5,
          maxScale: 12,
          scaleStep: 0.25,
          onVisibleChange: (open) => {
            if (!open) onClose();
          },
        }}
      />
    );

  const unavailable =
    error || (failed ? '预览暂时无法加载，链接可能已过期。' : asset && !asset.url ? '暂时没有可用的预览链接。' : '');
  return (
    <Modal
      open
      centered
      title={asset?.media_type === 'video' ? '视频预览' : asset?.media_type === 'audio' ? '音频预览' : '资产预览'}
      rootClassName="media-asset-preview"
      width={asset?.media_type === 'video' ? 'min(1200px, calc(100vw - 64px))' : 560}
      footer={null}
      onCancel={onClose}
      focusTriggerAfterClose={false}
    >
      {unavailable ? (
        <div className="media-asset-preview-state" role="alert">
          <p>{unavailable}</p>
          <Button onClick={() => setRevision((value) => value + 1)}>重新加载</Button>
        </div>
      ) : !asset ? (
        <div className="media-asset-preview-state" role="status" aria-label="正在加载资产预览">
          <Spin />
          <span>正在加载资产预览…</span>
        </div>
      ) : (
        <>
          <p className="media-asset-preview-name" title={asset.name}>
            {asset.name}
          </p>
          {asset.media_type === 'video' ? (
            <video
              ref={(element) => {
                player.current = element;
              }}
              src={asset.url!}
              controls
              controlsList="nodownload"
              playsInline
              preload="metadata"
              aria-label={asset.name}
              onError={() => setFailed(true)}
            />
          ) : (
            <audio
              ref={(element) => {
                player.current = element;
              }}
              src={asset.url!}
              controls
              controlsList="nodownload"
              preload="metadata"
              aria-label={asset.name}
              onError={() => setFailed(true)}
            />
          )}
        </>
      )}
    </Modal>
  );
}

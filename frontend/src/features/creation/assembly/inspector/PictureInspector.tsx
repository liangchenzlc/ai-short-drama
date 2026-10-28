import { AssemblyAction } from '@host/features/creation/assembly/export/AssemblyAction';
import type { FrameTransform } from '@host/types/creation/assembly/document';
import type { PictureInspectorProps } from '@host/types/creation/assembly/inspector/panels';
import { Button, InputNumber, Segmented, Slider } from 'antd';
import {
  RotateCcw
} from 'lucide-react';

export function PictureInspector({
  clip,
  pictureLocked,
  onMaterial,
  t,
  frameChange,
  editor
}: PictureInspectorProps) {
  return (<div className="assembly-inspector-fields">
    {clip ? (
      <>
        <div className="assembly-inspector-object">
          <strong>{clip.script || (clip.shot_id ? `镜头 ${clip.shot_position}` : '画面素材')}</strong>
          <Button size="small" disabled={pictureLocked} onClick={() => onMaterial(false, true)}>
            替换素材
          </Button>
        </div>
        <label>
          画面适配
          <Segmented
            value={t.fit}
            aria-label="画面适配"
            options={[
              { value: 'contain', label: '适应' },
              { value: 'cover', label: '填充' },
            ]}
            disabled={pictureLocked}
            onChange={(fit) => frameChange({ fit: fit as FrameTransform['fit'] })}
          />
        </label>
        <label>
          缩放
          <Slider
            aria-label="画面缩放"
            min={1}
            max={5}
            step={0.01}
            disabled={pictureLocked}
            value={t.scale}
            onChange={(scale) => frameChange({ scale })}
          />
        </label>
        <label>
          水平位置
          <Slider
            aria-label="画面水平位置"
            min={0}
            max={1}
            step={0.01}
            disabled={pictureLocked}
            value={t.x}
            onChange={(x) => frameChange({ x })}
          />
        </label>
        <label>
          垂直位置
          <Slider
            aria-label="画面垂直位置"
            min={0}
            max={1}
            step={0.01}
            disabled={pictureLocked}
            value={t.y}
            onChange={(y) => frameChange({ y })}
          />
        </label>
        <div className="assembly-inspector-row">
          <span>构图</span>
          <AssemblyAction
            label="重置构图"
            icon={<RotateCcw size={15} />}
            disabled={pictureLocked}
            onClick={() =>
              frameChange({
                fit: 'contain',
                scale: 1,
                x: 0.5,
                y: 0.5,
              })
            }
          />
        </div>
        {clip.source_type === 'image' && (
          <label>
            图片时长（秒）
            <InputNumber
              aria-label="图片展示时长"
              min={0.1}
              max={3600}
              value={(clip.display_duration_ms ?? 5000) / 1000}
              disabled={pictureLocked}
              onChange={(value) =>
                value &&
                editor.edit((state) => ({
                  ...state,
                  clips: state.clips?.map((c) =>
                    c.id === clip.id
                      ? {
                        ...c,
                        duration_ms: Math.round(value * 1000),
                        display_duration_ms: Math.round(value * 1000),
                        trim_in_ms: 0,
                        trim_out_ms: null,
                      }
                      : c,
                  ),
                }))
              }
            />
          </label>
        )}
      </>
    ) : (
      <div className="assembly-material-empty">在轨道中选择一个画面</div>
    )}
  </div>);
}

import { Icon } from '@host/shared/ui/Icon';
import type { StoryboardEditorHeaderProps } from '@host/types/creation/storyboard/workbench/views';
import { Button } from 'antd';
import { History, PanelRight } from 'lucide-react';

/** 工具栏只负责呈现与键盘绑定；切换前的保存、锁及焦点约束由工作台维护。 */
export function StoryboardEditorHeader({
  selectedShot,
  locked,
  selectionDisabled,
  editorTab,
  mediaTab,
  total,
  moreLoading,
  mediaSettingsOpen,
  mediaSettingsTrigger,
  onEditorTab,
  onEditorKeyDown,
  onAdjacent,
  onHistory,
  onToggleSettings,
  children,
}: StoryboardEditorHeaderProps) {
  return (
    <header className="standard-object-heading">
      <h3>
        {selectedShot
          ? `${String(selectedShot.position).padStart(2, '0')} · ${selectedShot.title || '未命名镜头'}`
          : '分镜制作'}
      </h3>
      <div className="storyboard-media-switch" role="tablist" aria-label="镜头制作内容">
        {[
          { key: 'image', label: '分镜图' },
          { key: 'video', label: '分镜视频' },
        ].map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            id={'storyboard-' + tab.key + '-tab'}
            aria-selected={editorTab === tab.key}
            aria-controls={'storyboard-' + tab.key + '-pane'}
            tabIndex={editorTab === tab.key ? 0 : -1}
            disabled={!!selectedShot && locked}
            onClick={() => onEditorTab(tab.key)}
            onKeyDown={onEditorKeyDown}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <nav aria-label="切换编辑镜头">
        <Button
          type="text"
          aria-label="上一个分镜"
          disabled={!selectedShot || selectionDisabled || selectedShot.position <= 1}
          onClick={() => onAdjacent(-1)}
          icon={<Icon name="back" size={16} />}
        />
        <Button
          type="text"
          aria-label="下一个分镜"
          loading={moreLoading}
          disabled={!selectedShot || selectionDisabled || total === null || selectedShot.position >= total}
          onClick={() => onAdjacent(1)}
          icon={<Icon name="arrow" size={16} />}
        />
        <Button
          type="text"
          aria-label={mediaTab === 'video' ? '视频生成记录' : '图片生成记录'}
          title="生成记录"
          disabled={!selectedShot}
          onClick={onHistory}
          icon={<History size={16} />}
        />
        {children}
        <Button
          ref={mediaSettingsTrigger}
          type="text"
          aria-label={mediaSettingsOpen ? '收起生成设置' : '展开生成设置'}
          aria-expanded={mediaSettingsOpen}
          aria-controls={mediaTab === 'video' ? 'storyboard-video-settings' : 'storyboard-image-settings'}
          onClick={onToggleSettings}
          icon={<PanelRight size={16} />}
        />
      </nav>
    </header>
  );
}

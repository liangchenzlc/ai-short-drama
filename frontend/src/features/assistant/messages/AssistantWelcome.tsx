import { Prompts, Welcome } from '@ant-design/x';
import { ArrowUpRight, Clapperboard } from 'lucide-react';
const starters = ['帮我梳理故事的节奏与冲突', '一起完善角色的动机与关系', '为当前作品提供创作建议'];
/** 新会话与暂不可访问共用同一块欢迎区，保证首屏与空会话视觉一致。 */
export function AssistantWelcome({
  accessEnded = false,
  readOnly = false,
  onPick,
}: {
  accessEnded?: boolean;
  readOnly?: boolean;
  onPick: (text: string) => void;
}) {
  return (
    <div className="ai-assistant-empty">
      <Welcome
        variant="borderless"
        icon={<Clapperboard size={26} aria-hidden="true" />}
        title={accessEnded ? '对话暂不可访问' : '把想法聊清楚'}
        description={
          accessEnded
            ? '当前无法读取，历史仍在服务端。恢复登录和项目访问后可重新读取。'
            : '一起梳理故事、画面和创作方向。'
        }
      />
      {accessEnded ? null : (
        <Prompts
          vertical
          items={starters.map((text) => ({
            key: text,
            label: (
              <button
                className="ai-assistant-starter"
                type="button"
                disabled={readOnly}
                onClick={() => onPick(text)}
              >
                <span>{text}</span>
                <ArrowUpRight size={15} />
              </button>
            ),
            disabled: readOnly,
          }))}
        />
      )}
    </div>
  );
}

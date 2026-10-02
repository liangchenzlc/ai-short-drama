import axios from 'axios';

export interface ApiFieldError { field: string; message: string }
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status?: number,
    public readonly fields: ApiFieldError[] = [],
    public readonly details?: { current_version?: string; references?: { type: string; id: string; name?: string }[]; reference_count?: number },
  ) { super(message); this.name = 'ApiError'; }
}
const fieldLabels: Record<string, string> = {
  name: '名称', provider: '提供商', model_key: '模型标识', base_url: '服务地址',
  apikey: 'API 密钥', enabled: '启用状态', service_type: '模型类别', row_version: '数据版本',
  config_id: '模型配置', 'input.prompt': '创作内容', 'input.messages': '文本消息',
  'input.reference_media_ids': '参考图片媒体 ID', 'input.first_frame_media_id': '首帧媒体 ID', 'input.last_frame_media_id': '尾帧媒体 ID',
  'parameters.aspect': '画面比例', 'parameters.resolution': '分辨率', 'parameters.count': '候选数量',
  'parameters.duration_ms': '视频时长', 'parameters.max_output_tokens': '最大输出 Token', 'parameters.temperature': '随机程度',
  'source.shot_id': '来源分镜 ID', 'source.asset_id': '来源素材 ID', 'source.row_version': '素材版本', 'target.id': '目标 ID', expected_media_id: '当前媒体 ID',
  title: '分集标题', synopsis: '梗概', aspect: '画幅', style: '风格',
};
const discoveryMessages: Record<string, string> = {
  model_discovery_auth: '模型服务认证失败，请检查 API 密钥及其权限。',
  model_discovery_unsupported: '此地址未提供兼容的模型列表接口，可以手动填写模型标识。',
  model_discovery_rate_limit: '模型服务请求过于频繁，请稍后重试。',
  model_discovery_unavailable: '无法连接模型服务，请检查服务地址或稍后重试。',
  model_discovery_timeout: '获取模型列表超时，请稍后重试；也可以手动填写。',
  model_discovery_redirect: '模型服务返回了跳转，请填写最终服务地址后重试。',
  model_discovery_address: '该服务地址无法用于探测，请使用有效的公开 HTTP(S) 地址。内网服务需由管理员配置。',
  model_discovery_credential: 'API 密钥格式不正确，请检查是否包含空格或换行。',
  model_discovery_key_address_changed: '服务地址已更改，请重新输入该地址的 API 密钥后获取模型。',
  model_discovery_invalid_response: '服务返回的内容不是有效模型列表，可以手动填写模型标识。',
  model_discovery_too_large: '模型列表响应过大，请手动填写模型标识。',
};
const workflowMessages: Record<string, string> = {
  authentication_required: '登录已失效，请重新登录后继续。',
  login_failed: '账号名或密码不正确，请核对后重试。',
  email_verification_required: '请先验证注册邮箱，再登录或接受项目邀请。',
  email_proof_invalid: '验证码无效或已过期，请重新获取验证码。',
  account_unavailable: '账号名或邮箱已被使用，请更换或找回已有账号。',
  invitation_identity_mismatch: '此链接属于另一个受邀账号，请切换到受邀账号。',
  invitation_unavailable: '邀请已过期或已撤销，请联系项目主人重新邀请。',
  already_member: '此账号已经拥有项目访问权限。',
  project_owner_required: '只有项目主人可以执行此操作。',
  task_actor_required: '此操作需要由任务发起者执行。',
  rate_limited: '操作过于频繁，请稍后再试。',
  csrf_failed: '请求验证未通过，请重新加载页面后重试。',
  project_version_conflict: '项目设置已被其他成员修改。输入仍保留，请重新读取后手动合并。',
  episode_version_conflict: '分集设置已被其他成员修改。输入仍保留，请重新读取后手动合并。',
  audio_retry_required: '请从声音面板核对当前台词后重新生成配音。',
  batch_retry_required: '请从批次详情选择失败项重新生成，以保持批次并发限制。',
  sound_review_required: '声音或视频剪辑已变化，请打开声音面板核对时间并确认。',
  audio_dialogue_stale: '存在未采用或已过期的配音，请校对台词并采用对应候选。',
  audio_timing: '声音时间范围无效或超出成片时长，请检查开始时间和裁剪范围。',
  subtitle_timing: '字幕超出成片时长，请检查字幕结束时间。',
  subtitle_invalid: 'SRT 格式有误：请使用 UTF-8 编码、正确时间格式，按时间排列且不能重叠。',
  subtitle_font_missing: '服务端尚未配置可用的中文字幕字体，请配置后重试，或先关闭字幕烧录。',
  audio_invalid: '音频无法解码或格式不支持，请选择有效的 MP3、WAV、M4A。',
  audio_limit: '配乐文件不能超过 100 MiB。',
  audio_dialogue: '请校对台词并填写有效音色标识。',
  audio_unavailable: '音频尚未验证或无法读取，请刷新候选。',
  audio_task_active: '此条台词已有活动任务或受理结果尚不确定，请先核对任务，避免重复生成。',
  invalid_image: '图片损坏或格式不受支持，请选择有效的 PNG、JPEG 或 WebP 图片（最多 4000 万像素）。',
  upload_too_large: '每张图片不能超过 20 MiB，请压缩后重新上传。',
  assembly_version_conflict: '成片草稿已在其他窗口修改。当前编辑已保留，请下载草稿后重新载入。',
  assembly_source_changed: '分镜来源已变化，请重新载入并核对后再操作。',
  assembly_trim_invalid: '裁剪范围超出视频实际时长，请调整起点和终点。',
  assembly_not_ready: '请补齐已勾选的视频、等待检测完成，并检查裁剪范围。',
  assembly_stale_source: '部分视频与当前分镜或草稿不同，请核对并确认使用旧版本。',
  assembly_clip_set: '片段列表已变化，请先下载草稿再重新载入。',
  assembly_limit: '已超过成片数量或时长上限，请减少片段后重试。',
  assembly_job_state: '任务状态已变化，请刷新导出记录。',
  assembly_idempotency_conflict: '导出请求发生冲突，请刷新记录检查是否已提交。',
  video_reference_required: '请先采用一张分镜参考图，再生成视频。',
  video_reference_stale: '分镜图已过期，请先核对并采用当前内容的图片。',
  generation_settings_changed: '生成设置已变化，请先保存并重新核对。',
  result_version_conflict: '提取候选已被其他页面修改。当前编辑已保留，请重新载入后核对。',
  candidate_unavailable: '候选不存在或已采用，请重新载入结果。',
  candidate_already_applied: '此候选已采用，不能更换采用方式。',
  duplicate_review_required: '发现同名或别名素材。请保存候选并重新核对匹配，选择复用或明确另建。',
  invalid_asset_reference: '此素材已不可复用，请重新载入结果核对。',
  result_not_ready: '提取结果尚未准备好，请稍后重试。',
  script_too_long: '剧本超过素材提取长度限制，请拆分分集或调整服务端提取限额。',
  script_empty: '请先填写并确认当前剧本。',
  writing_version_conflict: '小说或剧本版本已变化。草稿已保留，请重新加载后手动合并。',
  shot_version_conflict: '分镜已被其他窗口修改。输入已保留，请重新加载后合并。',
  storyboard_version_conflict: '分镜列表顺序已变化，请刷新列表后重试。',
  asset_version_conflict: '素材已被其他页面修改，请刷新素材后重试。',
  source_changed: '生成所基于的剧本已变化。候选仍保留，请基于当前剧本重新生成。',
  script_not_confirmed: '请先保存并确认当前编辑剧本。',
  asset_in_use: '素材仍被活动分镜引用，请先解除关联。',
  shared_asset_confirmation_required: '此素材被多处共享引用，需要明确确认影响范围。',
  stale_generation_source: '图片基于旧创作上下文生成，请核对后明确确认。',
  stale_source: '图片基于素材的旧内容生成，请核对后明确确认。',
  shot_archived: '分镜已归档，不能继续修改或采用图片。',
  result_already_applied: '此生成结果已经用另一种方式应用。',
  novel_empty: '请先填写并保存小说正文。',
};
export const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1', timeout: 15_000 });

http.interceptors.request.use(config => {
  if (!['get', 'head', 'options'].includes(config.method ?? 'get')) {
    const cookie = document.cookie.split('; ').find(value => value.startsWith('sd_csrf='));
    if (cookie) config.headers.set('X-CSRF-Token', decodeURIComponent(cookie.slice(8)));
  }
  return config;
});
// Never propagate AxiosError: its config can contain credentials. Do not reflect server input.
http.interceptors.response.use((response) => response, (cause: unknown) => {
  if (axios.isCancel(cause)) return Promise.reject(new ApiError('请求已取消', 'CANCELLED'));
  if (!axios.isAxiosError(cause)) return Promise.reject(new ApiError('请求失败，请重试。', 'UNKNOWN'));
  const status = cause.response?.status;
  if (status === 401 && !cause.config?.url?.startsWith("/auth/")) window.dispatchEvent(new Event("session-expired"));
  const envelope = cause.response?.data?.error;
  const fields: ApiFieldError[] = Array.isArray(envelope?.fields)
    ? envelope.fields.flatMap((entry: { field?: unknown }) => typeof entry?.field === 'string' && fieldLabels[entry.field]
      ? [{ field: entry.field, message: `请检查${fieldLabels[entry.field]}的填写内容。` }] : []) : [];
  const codeMessage = typeof envelope?.code === 'string' ? (discoveryMessages[envelope.code] ?? workflowMessages[envelope.code]) : undefined;
  const message = codeMessage || (status === 409 ? '数据已被修改，请重新加载最新内容后再操作。'
    : status === 404 ? '配置不存在或已被删除，请刷新列表。'
    : status === 422 ? '填写内容不符合要求，请检查后重试。'
    : status === 401 || status === 403 ? '没有操作权限，请检查登录状态。'
    : status === 429 ? '操作过于频繁，请稍后重试。'
    : status && status >= 500 ? '服务暂时不可用，请稍后重试。'
    : cause.code === 'ECONNABORTED' ? '请求超时，请检查网络后重试。'
    : !status ? '无法连接服务，请检查网络或服务是否启动。' : '请求失败，请重试。');
  const code = typeof envelope?.code === 'string' && /^[A-Z_]{1,64}$/i.test(envelope.code)
    ? envelope.code : status ? `HTTP_${status}` : 'NETWORK';
  const details = envelope?.details && typeof envelope.details === 'object' ? envelope.details : undefined;
  return Promise.reject(new ApiError(message, code, status, fields, details));
});
export const isCancelled = (error: unknown) => error instanceof ApiError && error.code === 'CANCELLED';
export const errorMessage = (error: unknown) => error instanceof ApiError ? error.message : '操作失败，请重试。';

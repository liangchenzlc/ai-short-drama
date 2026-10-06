const messages: Record<string, string> = {
    canvas_not_archived: "画布已恢复，请刷新项目列表",
    canvas_asset_folder_invalid: "素材分类 ID 无效，请刷新分类列表后重试",
    canvas_asset_folder_exists: "已存在同名素材分类",
    canvas_asset_invalid: "素材内容或查询条件无效，请检查后重试",
    canvas_asset_scope_conflict: "素材来自其他项目，请先创建当前项目的独立副本",
    canvas_asset_resource_conflict: "素材仍被画布引用，请解除引用后再替换资源",
    canvas_asset_reference_invalid: "已有画布媒体无法解析，已停止修改素材",
    canvas_asset_trash_conflict: "素材已不在回收站，未删除；请刷新列表",
    canvas_asset_in_use: "素材仍被画布、任务或业务记录引用，请先解除引用后再删除",
    canvas_asset_history_referenced: "素材仍被画布历史版本引用，不能彻底删除",
    canvas_resource_deleted: "此资源已彻底删除，请明确重新上传或复制",
    canvas_resource_copy_failed: "资源复制未完成，请保留原请求重试",
    canvas_resource_copy_changed: "复制来源已变化，未覆盖目标画布；请检查素材后重新复制",
    canvas_upload_conflict: "上传内容与原请求不一致，已保留本机文件；请使用原文件重试",
    canvas_upload_incomplete: "上传尚未完成或分片校验失败，请保留原文件重试",
    canvas_upload_too_large: "文件超过当前请求大小上限，请通过分片上传重试",
    canvas_upload_expired: "上传会话已过期，请用原上传标识重新开始",
    canvas_upload_busy: "待完成上传过多，请稍后重试",
    canvas_resource_invalid: "无法读取文件的有效媒体信息，请检查文件",
    canvas_resource_type_invalid: "文件类型与上传种类不符，请检查文件",
    canvas_resource_probe_unavailable: "音视频检测暂时失败，请稍后重试",
    canvas_viewport_conflict: "视口已在其他窗口更新，本机位置已保留；请重新加载后继续保存",
    canvas_view_preferences_conflict: "外观已在其他窗口更新，本机设置已保留；请重新加载后继续保存",
    canvas_private_identity_required: "含私人参数的重复条目缺少唯一稳定 ID；请修复条目身份后再保存",
    canvas_private_projection_upgrade_required: "旧私人数组缺少稳定身份，已保留原数据；请先显式迁移，不能按位置自动对应",
};

/** Host transport deliberately discards server text; publish only known canvas diagnostics. */
export function hostCanvasErrorMessage(code: string, fallback: string) {
    return Object.hasOwn(messages, code) ? messages[code] : fallback;
}

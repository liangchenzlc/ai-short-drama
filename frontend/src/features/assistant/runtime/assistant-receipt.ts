/** 只有能证明原请求尚未受理的错误才释放恢复记录；普通冲突仍需按原身份核对。 */
export function isAssistantAdmissionRejection(error: { code?: string; status?: number }): boolean {
  return (
    (error.status === 422 &&
      new Set([
        'validation_error',
        'unsupported_assistant_protocol',
        'invalid_selection',
        'selection_required',
        'unsupported_model_modality',
        'assistant_context_limit',
        'assistant_script_unbound',
        'assistant_script_scope',
        'assistant_attachment_scope',
        'agent_skills_too_large',
        'scope_mismatch',
      ]).has(error.code ?? '')) ||
    (error.status === 409 &&
      new Set([
        'assistant_model_frozen',
        'assistant_model_changed',
        'assistant_task_finished',
        'assistant_task_stopping',
        'assistant_task_settling',
        'assistant_control_changed',
        'assistant_selection_changed',
        'assistant_script_changed',
        'assistant_discussion_readonly',
        'assistant_task_not_paused',
        'assistant_resume_unavailable',
        'assistant_redirect_unavailable',
        'assistant_stop_unavailable',
      ]).has(error.code ?? ''))
  );
}

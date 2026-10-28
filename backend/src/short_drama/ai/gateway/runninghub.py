import mimetypes
import re
import time
from copy import deepcopy

from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_VIDEO,
    parse_runninghub_result,
    payload_code,
    runninghub_descriptor,
    runninghub_root,
    runninghub_submission,
    uploaded_filename,
    validate_task_id,
    workflow_prompt,
)
from short_drama.ai.adapters.runninghub.fields import (
    collect_fields,
    fields_for_mode,
    freeze_random_fields,
    validate_slots,
)
from short_drama.ai.gateway.base import GatewayBase
from short_drama.ai.gateway.canvas_credentials import CanvasCredentials
from short_drama.ai.gateway.support import _authentication
from short_drama.ai.gateway.transport import (
    MultipartBody,
)
from short_drama.ai.types import GenerationError


class RunningHubGateway(GatewayBase):
    def _submit_runninghub(self, snapshot, request, credential, adapter, loader, mask_loader):
        descriptor = runninghub_descriptor(snapshot, request, adapter)
        key, headers, secrets = _authentication(snapshot, credential, {}, adapter=adapter)
        budget = float(snapshot.get("budget_seconds", 300))
        if not 0 < budget <= 86400:
            raise GenerationError("invalid_budget")
        deadline = time.monotonic() + budget
        root = runninghub_root(snapshot["base_url"])
        if descriptor.kind == "workflow" and not descriptor.workflow_json:
            try:
                raw = self._json_request(
                    "POST",
                    root + "/api/openapi/getJsonApiFormat",
                    snapshot,
                    headers,
                    {"apiKey": key, "workflowId": descriptor.workflow_id or snapshot["model_key"]},
                    request_deadline=deadline,
                    paid_submission=False,
                )
                if payload_code(raw) not in {None, 0}:
                    raise GenerationError("invalid_workflow_schema")
                descriptor.workflow_json = workflow_prompt(raw, required=True)
            except GenerationError as error:
                # 固定源允许使用已保存映射继续；不吞未知程序错误或收费请求错误。
                if error.code not in {
                    "provider_auth",
                    "provider_endpoint",
                    "provider_rejected",
                    "upstream_unavailable",
                    "invalid_response",
                    "invalid_workflow_schema",
                    "connection_error",
                    "transport_error",
                    "dns_error",
                    "timeout",
                }:
                    raise
        if not descriptor.fields and descriptor.workflow_json:
            descriptor.fields = freeze_random_fields(
                collect_fields(descriptor.workflow_json, snapshot["service_type"])
            )
        descriptor.fields = fields_for_mode(descriptor.fields, snapshot["service_type"])
        inputs = request["canvas_request"]["input"]
        validate_slots(descriptor.fields, inputs)
        files = {}
        references = []
        for kind, group in (
            ("image", "referenceImages"),
            ("video", "referenceVideos"),
            ("audio", "referenceAudios"),
        ):
            references.extend(
                (kind, index, item, False) for index, item in enumerate(inputs.get(group, []))
            )
        if inputs.get("mask"):
            references.append(("image", 0, inputs["mask"], True))
        upload_key = (
            credential.runninghub_upload_key.get_secret_value()
            if isinstance(credential, CanvasCredentials) and credential.runninghub_upload_key
            else ""
        )
        if references and not upload_key:
            raise GenerationError("workflow_upload_key_missing")
        for kind, index, item, is_mask in references:
            limit = {"image": 50 * 1024**2, "video": 1024**3, "audio": 100 * 1024**2}[kind]
            if time.monotonic() >= deadline:
                raise GenerationError("workflow_upload_timeout")
            mime = item.get("type") or "application/octet-stream"
            if item.get("storageKey"):
                if is_mask:
                    if mask_loader is None:
                        raise GenerationError("unresolved_media_reference")
                    data = mask_loader(0, limit, deadline)
                else:
                    if loader is None:
                        raise GenerationError("unresolved_media_reference")
                    data = loader(kind, index, limit, deadline)
            else:
                data, mime = self.transport.download_media(
                    item.get("url", ""), limit, deadline=deadline
                )
            if not data or len(data) > limit:
                raise GenerationError("workflow_reference_invalid")
            identity = item.get("id") or item.get("storageKey") or item.get("url") or "reference"
            filename = (
                "reference-"
                + (re.sub(r"[^A-Za-z0-9_-]", "", identity)[:64] or "reference")
                + (mimetypes.guess_extension(mime, strict=False) or ".bin")
            )
            body = MultipartBody(
                [("apiKey", upload_key), ("fileType", "input"), ("file", (filename, data, mime))]
            )
            try:
                raw = self._json_request(
                    "POST",
                    root + "/task/openapi/upload",
                    snapshot,
                    headers,
                    body,
                    request_deadline=deadline,
                    paid_submission=False,
                )
            except GenerationError as error:
                if error.code == "provider_auth":
                    raise GenerationError(
                        "workflow_upload_auth", http_status=error.http_status
                    ) from None
                raise
            name = uploaded_filename(raw)
            if any(secret and secret in name for secret in secrets):
                raise GenerationError("invalid_workflow_upload")
            files[identity] = name
        prepared = {
            **request,
            "canvas_runninghub": descriptor.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
        }
        url, _, body = runninghub_submission(snapshot, prepared, adapter, files=files)
        resolved = deepcopy(body)
        body["apiKey"] = key
        raw = self._json_request(
            "POST",
            url,
            snapshot,
            headers,
            body,
            request_deadline=deadline,
            canvas_video=adapter == RUNNINGHUB_VIDEO,
        )
        result = parse_runninghub_result(raw, adapter, submitted=True, root=root)
        result.resolved_parameters = resolved
        for secret in secrets:
            result = self._sanitize(result, secret, submitted=True)
        return result

    def _poll_runninghub(self, snapshot, task_id, credential, adapter):
        validate_task_id(task_id)
        key, headers, secrets = _authentication(snapshot, credential, {}, adapter=adapter)
        root = runninghub_root(snapshot["base_url"])
        raw = self._json_request(
            "POST",
            root + "/task/openapi/outputs",
            snapshot,
            headers,
            {"apiKey": key, "taskId": task_id},
            paid_submission=False,
            canvas_video=adapter == RUNNINGHUB_VIDEO,
        )
        result = parse_runninghub_result(raw, adapter, submitted=False, task_id=task_id, root=root)
        for secret in secrets:
            result = self._sanitize(result, secret, submitted=False)
        return result

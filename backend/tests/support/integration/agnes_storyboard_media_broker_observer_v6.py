"""分镜批量真实媒体的按任务观测；所有调用委托正式 Gateway 和 SafeTransport。"""

import base64
import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from celery.signals import task_postrun, task_prerun

from short_drama.ai import GenerationGateway
from short_drama.ai.types import GenerationError
from short_drama.tasks import worker

directory = Path(os.environ["AGNES_STORYBOARD_MEDIA_BROKER_DIRECTORY"]).resolve()
assert directory.is_dir()
lock = threading.RLock()
current = threading.local()
deliveries = {}
reports = {}


def checked_json(value):
    serialized = json.dumps(value, ensure_ascii=False, indent=2)
    if any(
        secret
        and (secret in serialized or json.dumps(secret, ensure_ascii=False)[1:-1] in serialized)
        for secret in getattr(current, "secrets", [])
    ):
        raise RuntimeError("Credential detected in observer JSON; evidence write blocked")
    return serialized


def checked_binary(data):
    if any(secret and secret.encode("utf-8") in data for secret in getattr(current, "secrets", [])):
        raise RuntimeError("Credential detected in supplier bytes; evidence write blocked")
    return data


def write_json(path, value):
    serialized = checked_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)


def task_report():
    identity = (current.role, current.task_id)
    return reports[identity], directory / "provider" / identity[0] / identity[1]


def checkpoint():
    report, target = task_report()
    write_json(target / "report.json", report)


def worker_checkpoint(role):
    write_json(
        directory / (role + "-worker-report.json"),
        {
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "role": role,
            "deliveries": deliveries[role],
        },
    )


def started(sender=None, task_id=None, task=None, args=None, **kwargs):
    if sender.name != "short_drama.execute_generation":
        return
    role = task.request.delivery_info["routing_key"]
    assert role in {"image", "video"}, "This observer only accepts storyboard media tasks"
    identifier = str(args[0])
    assert identifier.isdecimal()
    current.role, current.task_id, current.secrets = role, identifier, []
    with lock:
        reports.setdefault(
            (role, identifier),
            {
                "scope": "real Agnes through formal generation worker; no provider hold",
                "role": role,
                "task_id": identifier,
                "worker_pid": os.getpid(),
                "submissions": 0,
                "requests": [],
                "downloads": [],
                "inline_outputs": [],
                "results": [],
            },
        )
        deliveries.setdefault(role, []).append(
            {
                "celery_task_id": task_id,
                "task_id": identifier,
                "message_version": str(args[1]),
                "delivery": task.request.delivery_info,
                "completed": False,
            }
        )
        checkpoint()
        worker_checkpoint(role)


def finished(sender=None, task_id=None, state=None, **kwargs):
    if sender.name != "short_drama.execute_generation":
        return
    with lock:
        row = next(row for row in deliveries[current.role] if row["celery_task_id"] == task_id)
        row.update(completed=True, state=state)
        worker_checkpoint(current.role)
    current.secrets = []


def safe(value):
    if isinstance(value, str):
        for secret in current.secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
    return value


def remember_credential(credential):
    if isinstance(credential, str):
        current.secrets = [credential]
    else:
        current.secrets = [credential.api_key.get_secret_value()]
        current.secrets.extend(item.value.get_secret_value() for item in credential.headers)


class ObservedTransport:
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def request(self, method, url, **kwargs):
        parts = urlsplit(url)
        body = kwargs.get("body")
        item = {
            "method": method,
            "host": parts.hostname,
            "path": parts.path,
            "started_at": datetime.now(UTC).isoformat(),
        }
        if isinstance(body, dict):
            item["fields"] = sorted(body)
            item["options"] = {
                key: safe(body[key])
                for key in ("model", "mode", "size", "ratio", "seconds", "aspect_ratio")
                if key in body
            }
            if isinstance(body.get("prompt"), str):
                item["prompt_sha256"] = hashlib.sha256(body["prompt"].encode()).hexdigest()
            item["image_references"] = [
                hashlib.sha256(base64.b64decode(ref.split(",", 1)[-1])).hexdigest()
                for ref in (body.get("extra_body") or {}).get("image", [])
            ]
        with lock:
            report, _ = task_report()
            report["requests"].append(item)
            checkpoint()
        try:
            status, headers, data = self.delegate.request(method, url, **kwargs)
        except GenerationError as error:
            with lock:
                item["error_code"] = safe(error.code)
                checkpoint()
            raise
        with lock:
            item.update(
                http_status=status,
                response_bytes=len(data),
                finished_at=datetime.now(UTC).isoformat(),
            )
            if parts.path in {"/v1/videos", "/agnesapi"}:
                try:
                    response = json.loads(data)
                except (ValueError, UnicodeError):
                    response = {}
                if isinstance(response, dict):
                    item["public_status"] = safe(response.get("status"))
                    item["video_id"] = safe(response.get("video_id"))
                    item["queue_full"] = response.get("code") == "video_queue_full"
            checkpoint()
        return status, headers, data

    def download_media(self, url, max_bytes, **kwargs):
        data, mime = self.delegate.download_media(url, max_bytes, **kwargs)
        checked_binary(data)
        return data, mime


class ObservedMediaGateway(GenerationGateway):
    def __init__(self, settings):
        super().__init__(settings)
        self.worker_id = settings.snowflake_worker_id
        self.transport = ObservedTransport(self.transport)

    def download_media(self, url, max_bytes, *, snapshot=None, credential=None):
        if credential is not None:
            remember_credential(credential)
        data, mime = super().download_media(
            url, max_bytes, snapshot=snapshot, credential=credential
        )
        checked_binary(data)
        with lock:
            report, target = task_report()
            filename = f"supplier-download-{len(report['downloads']) + 1}.bin"
            (target / filename).write_bytes(data)
            report["downloads"].append(
                {
                    "file": filename,
                    "bytes": len(data),
                    "mime": safe(mime),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            checkpoint()
        return data, mime

    def record_result(self, result, action):
        with lock:
            report, target = task_report()
            for output in result.outputs:
                if "base64" in output:
                    data = base64.b64decode(output["base64"].split(",", 1)[-1], validate=True)
                    checked_binary(data)
                    filename = f"supplier-inline-{len(report['inline_outputs']) + 1}.bin"
                    (target / filename).write_bytes(data)
                    report["inline_outputs"].append(
                        {
                            "file": filename,
                            "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
            report["results"].append(
                {
                    "action": action,
                    "status": result.status,
                    "provider_task_id": safe(result.provider_task_id),
                }
            )
            checkpoint()

    def record_error(self, error):
        with lock:
            report, _ = task_report()
            report.setdefault("errors", []).append(
                {
                    "code": safe(error.code),
                    "http_status": error.http_status,
                    "accepted_unknown": error.accepted_unknown,
                }
            )
            checkpoint()

    def submit(self, snapshot, request, credential, adapter=None, **kwargs):
        remember_credential(credential)
        with lock:
            report, _ = task_report()
            report.update(
                model=snapshot["model_key"], adapter=adapter, snowflake_worker_id=self.worker_id
            )
            report["submissions"] += 1
            checkpoint()
        try:
            result = super().submit(snapshot, request, credential, adapter, **kwargs)
        except GenerationError as error:
            self.record_error(error)
            raise
        self.record_result(result, "submit")
        return result

    def poll(self, snapshot, provider_task_id, credential, adapter):
        remember_credential(credential)
        try:
            result = super().poll(snapshot, provider_task_id, credential, adapter)
        except GenerationError as error:
            self.record_error(error)
            raise
        self.record_result(result, "poll")
        return result


worker.GenerationGateway = ObservedMediaGateway
task_prerun.connect(started, weak=False)
task_postrun.connect(finished, weak=False)

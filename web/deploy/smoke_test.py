#!/usr/bin/env python3
"""Exercise the deployed Web/API/PostgreSQL path without third-party clients."""

from __future__ import annotations

import argparse
import io
import json
import os
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path


BASE_URL = os.environ.get("C2B_SMOKE_BASE_URL", "http://127.0.0.1:8080/api")


def request_bytes(path: str, *, method: str = "GET", data: bytes | None = None,
                  headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc
    return body


def request_json(path: str, *, method: str = "GET", data: bytes | None = None,
                 headers: dict[str, str] | None = None) -> object:
    body = request_bytes(path, method=method, data=data, headers=headers)
    return json.loads(body) if body else {}


def post_json(path: str, payload: dict) -> object:
    return request_json(
        path,
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )


def wait_until_ready(timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            request_json("/projects")
            return
        except Exception as exc:  # stack startup is expected to race briefly
            error = exc
            time.sleep(2)
    raise RuntimeError(f"Web stack did not become ready: {error}")


def fixture_zip() -> bytes:
    java = """\
package smoke;
@RestController
@RequestMapping("/demo")
class DemoController {
    @GetMapping("/users")
    void users(int userId) {
        if (userId <= 0) {
            throw new IllegalArgumentException("userId must be positive");
        }
    }
}
"""
    mapper = """\
<mapper namespace="smoke.DemoMapper">
  <select id="listUsers">SELECT * FROM sys_user</select>
  <update id="disableUser">UPDATE sys_user SET status = '1' WHERE user_id = 1</update>
</mapper>
"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("smoke/DemoController.java", java)
        archive.writestr("smoke/DemoMapper.xml", mapper)
    return output.getvalue()


def multipart_project() -> tuple[bytes, str]:
    boundary = f"----c2b-smoke-{uuid.uuid4().hex}"
    zip_bytes = fixture_zip()
    chunks = [
        (
            f"--{boundary}\r\n"
            "Content-Disposition: form-data; name=\"name\"\r\n\r\n"
            "CI Docker smoke\r\n"
        ).encode(),
        (
            f"--{boundary}\r\n"
            "Content-Disposition: form-data; name=\"file\"; "
            "filename=\"smoke.zip\"\r\n"
            "Content-Type: application/zip\r\n\r\n"
        ).encode(),
        zip_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), boundary


def assert_graph(project_id: int) -> None:
    apis = request_json(f"/projects/{project_id}/apis")
    tables = request_json(f"/projects/{project_id}/tables")
    sql_ops = request_json(f"/projects/{project_id}/sql-ops")
    if not any(item.get("method") == "GET" and item.get("path") == "/demo/users"
               for item in apis):
        raise AssertionError(f"Expected GET /demo/users, got {apis!r}")
    if not any(item.get("name") == "sys_user" for item in tables):
        raise AssertionError(f"Expected sys_user table, got {tables!r}")
    operations = {item.get("op") for item in sql_ops
                  if item.get("table") == "sys_user"}
    if not {"select", "update"}.issubset(operations):
        raise AssertionError(f"Expected sys_user select/update, got {sql_ops!r}")


def wait_for_job(project_id: int, job_id: int, timeout: int = 180) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = request_json(f"/projects/{project_id}/label-jobs/{job_id}")
        if job["status"] == "done":
            return job
        if job["status"] == "failed":
            raise RuntimeError(f"Background job failed: {job.get('error')}")
        time.sleep(1)
    raise RuntimeError(f"Background job {job_id} did not finish in {timeout} seconds")


def assert_semantic_outputs(project_id: int, rule_id: int) -> None:
    rules = request_json(f"/projects/{project_id}/rules")
    if not any(int(item["id"]) == rule_id for item in rules):
        raise AssertionError("Mock semantic rule is missing")
    evidence = request_json(
        f"/projects/{project_id}/runtime-evidence?target_type=rule&target_id={rule_id}"
    )
    if evidence["runtime_level"] != "R1" or not evidence["items"]:
        raise AssertionError(f"R1 runtime evidence is missing: {evidence!r}")
    document = request_bytes(
        f"/projects/{project_id}/documents/rules?draft=true"
    ).decode("utf-8")
    if "业务规则" not in document or "R1" not in document:
        raise AssertionError("Draft rules document did not include semantic/runtime data")


def create(state_file: Path) -> None:
    wait_until_ready()
    body, boundary = multipart_project()
    result = request_json(
        "/projects/import",
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    project_id = int(result["project_id"])
    if result.get("import_status") != "ready" or result.get("job_id") is not None:
        raise AssertionError(f"ZIP import unexpectedly started scanning: {result!r}")
    pending = request_json(f"/projects/{project_id}/pipeline")
    scan_step = next(step for step in pending["steps"] if step["key"] == "scan")
    if scan_step["status"] != "pending":
        raise AssertionError(f"Expected a pending scan after import: {pending!r}")
    scan = post_json(f"/projects/{project_id}/scan", {})
    job_id = int(scan["job_id"])
    wait_for_job(project_id, job_id)
    assert_graph(project_id)

    post_json("/settings/llm", {"mode": "mock"})
    label = post_json(
        f"/projects/{project_id}/label",
        {"limit": 5, "concurrency": 1, "entry_only": False},
    )
    wait_for_job(project_id, int(label["job_id"]))
    rules = request_json(f"/projects/{project_id}/rules")
    if not rules:
        raise AssertionError("Mock semantic labeling produced no rules")
    rule_id = int(rules[0]["id"])
    runtime = post_json(
        f"/projects/{project_id}/runtime-evidence",
        {
            "target_type": "rule",
            "target_id": rule_id,
            "level": "R1",
            "evidence_type": "test",
            "environment": "test",
            "summary": "GitHub Actions Docker/PostgreSQL smoke",
            "reference": "CI docker-postgres-smoke",
            "created_by": "github-actions",
        },
    )
    assert_semantic_outputs(project_id, rule_id)
    state_file.write_text(
        json.dumps({
            "project_id": project_id,
            "rule_id": rule_id,
            "evidence_id": int(runtime["item"]["id"]),
        }),
        encoding="utf-8",
    )
    print(
        f"Imported, scanned, labeled and exported project {project_id} "
        "through nginx/API/PostgreSQL."
    )


def verify_delete(state_file: Path) -> None:
    wait_until_ready()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    project_id = int(state["project_id"])
    rule_id = int(state["rule_id"])
    evidence_id = int(state["evidence_id"])
    projects = request_json("/projects")
    if not any(int(item["id"]) == project_id for item in projects):
        raise AssertionError("Project did not survive API container restart")
    assert_graph(project_id)
    assert_semantic_outputs(project_id, rule_id)
    deleted = request_json(
        f"/projects/{project_id}/runtime-evidence/{evidence_id}", method="DELETE"
    )
    if deleted["runtime_level"] != "R0":
        raise AssertionError(f"Runtime evidence delete did not restore R0: {deleted!r}")
    request_json(f"/projects/{project_id}", method="DELETE")
    projects = request_json("/projects")
    if any(int(item["id"]) == project_id for item in projects):
        raise AssertionError("Project deletion did not remove the database record")
    print(f"Verified persistence and deletion for project {project_id}.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("create", "verify-delete"))
    parser.add_argument("--state-file", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "create":
        create(args.state_file)
    else:
        verify_delete(args.state_file)


if __name__ == "__main__":
    main()

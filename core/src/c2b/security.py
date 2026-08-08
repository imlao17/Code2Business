"""Security boundaries for imported repositories and outbound integrations.

The API accepts two kinds of untrusted input that cross a process boundary:
archives/repository URLs and an OpenAI-compatible base URL.  Keeping the
validation here makes the CLI, API and LLM client use the same policy.
"""
from __future__ import annotations

import ipaddress
import shutil
import socket
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit


class UnsafeUrlError(ValueError):
    """Raised when an outbound URL can reach an unsafe destination."""


class UnsafeArchiveError(ValueError):
    """Raised when an archive violates extraction limits."""


def project_storage_path(data_root: Path) -> Path:
    """Return a server-generated project path below ``data_root/projects``.

    Display names are intentionally not used as filesystem identifiers: they
    may contain path separators, collide, or later be renamed.
    """
    return data_root.resolve() / "projects" / uuid.uuid4().hex


def _unsafe_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _resolved_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"无法解析目标主机: {host}") from exc
    addresses = {row[4][0].split("%", 1)[0] for row in rows}
    if not addresses:
        raise UnsafeUrlError(f"目标主机没有可用地址: {host}")
    return tuple(sorted(addresses))


def validate_outbound_url(
    url: str,
    *,
    purpose: str,
    allow_private: bool = False,
) -> str:
    """Validate and normalize an outbound LLM or Git URL.

    Public HTTPS is the safe default.  Local OpenAI-compatible services such
    as Ollama require both an explicit ``allow_private`` opt-in and may then
    use HTTP.  Credentials and fragments are never accepted because they are
    easy to leak into logs and error messages.
    """
    value = (url or "").strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("URL 格式无效") from exc

    if purpose not in {"llm", "git"}:
        raise ValueError(f"unknown URL purpose: {purpose}")
    if parsed.scheme not in ({"https", "http"} if allow_private else {"https"}):
        raise UnsafeUrlError("仅允许 HTTPS；访问本地服务需要显式开启私网地址")
    if not parsed.hostname:
        raise UnsafeUrlError("URL 缺少主机名")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL 不允许内嵌用户名或密码")
    if parsed.fragment:
        raise UnsafeUrlError("URL 不允许包含片段标识")

    host = parsed.hostname.rstrip(".").lower()
    check_port = port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = (str(ipaddress.ip_address(host)),)
    except ValueError:
        addresses = _resolved_addresses(host, check_port)
    if not allow_private and any(_unsafe_ip(address) for address in addresses):
        raise UnsafeUrlError("目标地址属于本机或私有网络，已拒绝")

    # urlsplit keeps IPv6 brackets in netloc, so rebuilding preserves a valid
    # authority while dropping an empty fragment.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def safe_extract_zip(
    archive: zipfile.ZipFile,
    destination: Path,
    *,
    max_members: int = 100_000,
    max_uncompressed_bytes: int = 10 * 1024**3,
    max_compression_ratio: int = 200,
) -> None:
    """Extract ``archive`` with traversal, symlink and zip-bomb protection."""
    root = destination.resolve()
    infos = archive.infolist()
    if len(infos) > max_members:
        raise UnsafeArchiveError(f"压缩包文件数超过限制（{max_members:,}）")

    total_size = sum(info.file_size for info in infos)
    if total_size > max_uncompressed_bytes:
        raise UnsafeArchiveError(
            f"压缩包解压后超过限制（{max_uncompressed_bytes:,} 字节）"
        )

    for info in infos:
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise UnsafeArchiveError(f"压缩包不允许符号链接: {info.filename}")
        if (
            info.file_size > 0
            and info.compress_size > 0
            and info.file_size / info.compress_size > max_compression_ratio
        ):
            raise UnsafeArchiveError(f"压缩率异常，疑似 zip bomb: {info.filename}")

        normalized_name = info.filename.replace("\\", "/")
        member_path = PurePosixPath(normalized_name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise UnsafeArchiveError(f"非法的 zip 路径: {info.filename}")
        target = root.joinpath(*member_path.parts).resolve()
        if not target.is_relative_to(root):
            raise UnsafeArchiveError(f"非法的 zip 路径: {info.filename}")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info, "r") as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)

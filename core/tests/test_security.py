import asyncio
import io
import tempfile
import unittest
import zipfile
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class SecurityBoundaryTests(unittest.TestCase):
    def test_optional_api_token_blocks_unauthenticated_requests(self):
        from c2b.api.main import _optional_api_token

        async def downstream(_request):
            return "allowed"

        missing = SimpleNamespace(
            url=SimpleNamespace(path="/api/projects"), method="GET", headers={}
        )
        valid = SimpleNamespace(
            url=SimpleNamespace(path="/api/projects"),
            method="GET",
            headers={"authorization": "Bearer secret-token"},
        )
        with patch.dict("os.environ", {"C2B_API_TOKEN": "secret-token"}):
            denied = asyncio.run(_optional_api_token(missing, downstream))
            allowed = asyncio.run(_optional_api_token(valid, downstream))
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed, "allowed")

    def test_outbound_url_rejects_private_network_and_credentials(self):
        from c2b.security import UnsafeUrlError, validate_outbound_url

        for url in (
            "http://127.0.0.1:11434/v1",
            "http://169.254.169.254/latest/meta-data",
            "https://user:secret@example.com/v1",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                validate_outbound_url(url, purpose="llm")

        self.assertEqual(
            validate_outbound_url("https://8.8.8.8/v1", purpose="llm"),
            "https://8.8.8.8/v1",
        )

    def test_private_network_can_only_be_enabled_explicitly(self):
        from c2b.security import validate_outbound_url

        self.assertEqual(
            validate_outbound_url(
                "http://127.0.0.1:11434/v1", purpose="llm", allow_private=True
            ),
            "http://127.0.0.1:11434/v1",
        )

    def test_project_storage_path_does_not_use_user_name(self):
        from c2b.security import project_storage_path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = project_storage_path(root)
            self.assertEqual(target.parent, root.resolve() / "projects")
            self.assertNotIn("..", target.parts)
            self.assertRegex(target.name, r"^[0-9a-f]{32}$")

    def test_zip_extraction_rejects_path_traversal(self):
        from c2b.security import UnsafeArchiveError, safe_extract_zip

        for member in ("../outside.txt", "..\\outside.txt", "/absolute.txt"):
            with self.subTest(member=member):
                payload = io.BytesIO()
                with zipfile.ZipFile(payload, "w") as zf:
                    zf.writestr(member, "owned")
                payload.seek(0)
                with tempfile.TemporaryDirectory() as td:
                    with zipfile.ZipFile(payload) as zf:
                        with self.assertRaises(UnsafeArchiveError):
                            safe_extract_zip(zf, Path(td))
                    self.assertFalse((Path(td).parent / "outside.txt").exists())

    def test_zip_extraction_enforces_uncompressed_limit(self):
        from c2b.security import UnsafeArchiveError, safe_extract_zip

        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("large.txt", "x" * 4096)
        payload.seek(0)
        with tempfile.TemporaryDirectory() as td:
            with zipfile.ZipFile(payload) as zf:
                with self.assertRaises(UnsafeArchiveError):
                    safe_extract_zip(zf, Path(td), max_uncompressed_bytes=128)

    def test_zip_extraction_rejects_symbolic_links(self):
        from c2b.security import UnsafeArchiveError, safe_extract_zip

        payload = io.BytesIO()
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(payload, "w") as zf:
            zf.writestr(link, "../../outside")
        payload.seek(0)
        with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(payload) as zf:
            with self.assertRaises(UnsafeArchiveError):
                safe_extract_zip(zf, Path(td))


if __name__ == "__main__":
    unittest.main()

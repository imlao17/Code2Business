from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from c2b.scanner.probe import probe


class ProbeSamplingTests(unittest.TestCase):
    def test_probe_samples_more_than_first_regular_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for idx in range(1, 42):
                path = root / f"File{idx:02d}.java"
                if idx == 41:
                    path.write_bytes(
                        "class File41 { String value = \"あ\"; }\n".encode("shift_jis")
                    )
                else:
                    path.write_text(
                        f"class File{idx:02d} {{ int value = {idx}; }}\n",
                        encoding="utf-8",
                    )

            report = probe(root)

            self.assertEqual(report["total_files"], 41)
            self.assertGreaterEqual(sum(report["encoding_samples"].values()), 2)
            self.assertEqual(report["encoding_samples"].get("utf-8"), 1)
            self.assertEqual(report["encoding_samples"].get("shift_jis"), 1)


if __name__ == "__main__":
    unittest.main()

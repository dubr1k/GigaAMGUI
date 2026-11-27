import hashlib
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

import unpack


class UnpackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.output = self.root / "model"

    def make_archive(self, mutate=None, extra=None, symlink_part=False, provenance=None):
        model_id, repo, revision = unpack.MODELS["v4"]
        contents = {name: (name + "\n").encode() for name in sorted(unpack.REQUIRED)}
        contents["MODEL_PROVENANCE.json"] = json.dumps(
            provenance if provenance is not None else {"repo": repo, "revision": revision, "license": "MIT"}
        ).encode()
        entries = [
            {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in contents.items()
        ]
        data = b"".join(contents.values())
        asset = f"{model_id}-part-001.bin"
        metadata = {
            "version": 1,
            "model_id": model_id,
            "parts": [{
                "url": f"https://github.com/dubr1k/GigaAMGUI/releases/download/rust-models-v1/{asset}",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "files": entries,
            }],
        }
        if mutate:
            mutate(metadata)
        path = self.artifacts / "download"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", json.dumps(metadata))
            if symlink_part:
                info = zipfile.ZipInfo(asset)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, data)
            else:
                archive.writestr(asset, data)
            if extra:
                archive.writestr(extra, b"unexpected")
        raw = path.read_bytes()
        return contents, len(raw), hashlib.sha256(raw).hexdigest()

    def extract(self, size, digest):
        unpack.unpack("v4", self.artifacts, self.output, size, digest)

    def test_unpacks_verified_files(self):
        contents, size, digest = self.make_archive()
        self.extract(size, digest)
        self.assertEqual({path.name for path in self.output.iterdir()}, set(contents))
        for name, data in contents.items():
            self.assertEqual((self.output / name).read_bytes(), data)

    def test_rejects_bad_file_and_part_hashes_without_partial_output(self):
        for field in ("file", "part"):
            with self.subTest(field=field):
                def mutate(metadata, field=field):
                    part = metadata["parts"][0]
                    target = part["files"][-1] if field == "file" else part
                    target["sha256"] = "0" * 64

                _, size, digest = self.make_archive(mutate=mutate)
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    self.extract(size, digest)
                self.assertFalse(self.output.exists())

    def test_rejects_unsafe_metadata_before_creating_output(self):
        def traversal(metadata):
            metadata["parts"][0]["files"][0]["name"] = "../escape"

        def case_duplicate(metadata):
            files = metadata["parts"][0]["files"]
            files[-1]["name"] = files[0]["name"].lower()

        def oversized(metadata):
            metadata["parts"][0]["size"] = unpack.MAX_PART + 1

        def boolean_version(metadata):
            metadata["version"] = True

        for mutate in (traversal, case_duplicate, oversized, boolean_version):
            with self.subTest(mutation=mutate.__name__):
                _, size, digest = self.make_archive(mutate=mutate)
                with self.assertRaises(ValueError):
                    self.extract(size, digest)
                self.assertFalse(self.output.exists())
                self.assertFalse((self.root / "escape").exists())

    def test_rejects_unlisted_and_symlink_archive_members(self):
        for options in ({"extra": "extra.bin"}, {"extra": "../escape"}, {"symlink_part": True}):
            with self.subTest(options=options):
                _, size, digest = self.make_archive(**options)
                with self.assertRaises(ValueError):
                    self.extract(size, digest)
                self.assertFalse(self.output.exists())
                self.assertFalse((self.root / "escape").exists())

    def test_rejects_wrong_archive_digest_and_model_provenance(self):
        _, size, _ = self.make_archive()
        with self.assertRaisesRegex(ValueError, "archive SHA-256"):
            self.extract(size, "0" * 64)
        self.assertFalse(self.output.exists())

        _, size, digest = self.make_archive(provenance={"repo": "wrong", "revision": "wrong", "license": "MIT"})
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.extract(size, digest)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

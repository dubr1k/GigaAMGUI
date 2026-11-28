import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rust_liquid_release_gate", ROOT / "scripts/rust_liquid_release_gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

SHA = "a" * 40
TAG = "v2.3.4"
CANDIDATE = 101
PROOF = 102
BUILD = 103
REPOSITORY = "owner/repo"
REPOSITORY_ID = 456


def zipped(name, content, extras=()):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
        for extra_name, extra_content in extras:
            archive.writestr(extra_name, extra_content)
    return out.getvalue()


def job(run_id, name, steps=()):
    return {
        "run_id": run_id, "run_attempt": 1, "head_sha": SHA, "name": name,
        "status": "completed", "conclusion": "success",
        "steps": [{"name": step, "status": "completed", "conclusion": "success"} for step in steps],
    }


class Response(io.BytesIO):
    def __init__(self, content=b"", status=200, headers=None):
        super().__init__(content)
        self.status = status
        self.headers = headers or {}


class FakeAPI:
    def __init__(self, blobs):
        self.blobs = blobs
        self.requests = []
        self.location = None

    def open(self, request, timeout):
        self.requests.append(request)
        artifact_id = int(urllib.parse.urlsplit(request.full_url).path.split("/")[-2])
        location = self.location or f"https://storage.example/{artifact_id}?signed=private"
        return Response(status=302, headers={"Location": location})


class FakeBlob:
    def __init__(self, blobs):
        self.blobs = blobs
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        artifact_id = int(urllib.parse.urlsplit(request.full_url).path.rsplit("/", 1)[-1])
        content = self.blobs[artifact_id]
        return Response(content, headers={"Content-Length": str(len(content))})


class FakeGitHub(gate.GitHub):
    def __init__(self):
        super().__init__(REPOSITORY, str(REPOSITORY_ID), "https://api.github.com", "secret-token")
        self.blobs = {}
        self.api = FakeAPI(self.blobs)
        self.blob = FakeBlob(self.blobs)
        repo = {"id": REPOSITORY_ID, "full_name": REPOSITORY}
        def run(run_id, path, event, branch):
            return {"id": run_id, "path": path, "repository": repo.copy(), "head_repository": repo.copy(),
                    "head_sha": SHA, "head_branch": branch, "event": event, "status": "completed",
                    "conclusion": "success", "run_attempt": 1}
        self.runs = {
            CANDIDATE: run(CANDIDATE, ".github/workflows/rust-liquid.yml", "push", "main"),
            PROOF: run(PROOF, ".github/workflows/rust-liquid-ui-proof.yml", "workflow_dispatch", "main"),
            BUILD: run(BUILD, ".github/workflows/build.yml", "push", TAG),
        }
        self.jobs = {
            CANDIDATE: [job(CANDIDATE, "Build and compare pinned FFmpeg binaries")] + [
                job(CANDIDATE, f"Rust Liquid {platform} candidate") for platform in gate.PLATFORMS
            ],
            PROOF: [
                job(PROOF, "Installed candidate UI (linux-x64)", (
                    "Exercise Linux UI and save a real transcript", "Prepare Linux long-proof models through the installed UI",
                    "Exercise Linux packaged long diarization", "Exercise both new models through the installed Linux UI",
                    "Verify automatic Community-1 delivery through the installed Linux UI",
                    "Verify incomplete Community-1 bundle through the installed Linux UI",
                    "Exercise Community-1 through the installed Linux UI")),
                job(PROOF, "Installed candidate UI (windows-x64)", (
                    "Exercise Windows UI and save a real transcript", "Prepare Windows long-proof models through the installed UI",
                    "Exercise Windows packaged long diarization", "Exercise both new models through the installed Windows UI",
                    "Verify automatic Community-1 delivery through the installed Windows UI",
                    "Verify incomplete Community-1 bundle through the installed Windows UI",
                    "Exercise Community-1 through the installed Windows UI")),
            ],
            BUILD: [job(BUILD, name) for name in (
                "TUI tests", "Windows x64", "macOS arm64", "Linux x64", "macOS arm64 full app bundle",
                "macOS x86_64 (Intel) ONNX app bundle", "Native Swift macOS arm64 app",
            )],
        }
        self.artifacts = {}
        self.payloads = {platform: f"real {platform} package".encode() for platform in gate.PLATFORMS}
        for platform, asset in gate.PLATFORMS.items():
            payload = self.payloads[platform]
            attestation = {"version": 1, "platform": platform, "candidate_run_id": CANDIDATE,
                           "candidate_sha": SHA, "asset_name": asset, "asset_sha256": hashlib.sha256(payload).hexdigest()}
            self.add(PROOF, f"rust-liquid-ui-attestation-{platform}", zipped(f"{platform}.json", json.dumps(attestation)))
            self.add(CANDIDATE, f"rust-liquid-{platform}-candidate", zipped(asset, payload))
        self.tag_reads = 0
        self.moved_tag = False

    def add(self, run_id, name, content):
        artifact_id = len(self.blobs) + 1
        self.blobs[artifact_id] = content
        self.artifacts[(run_id, name)] = [{
            "id": artifact_id, "name": name, "size_in_bytes": len(content), "expired": False,
            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            "workflow_run": {"id": run_id, "repository_id": REPOSITORY_ID,
                             "head_repository_id": REPOSITORY_ID, "head_sha": SHA},
        }]

    def replace(self, run_id, name, content):
        artifact = self.artifacts[(run_id, name)][0]
        self.blobs[artifact["id"]] = content
        artifact["size_in_bytes"] = len(content)
        artifact["digest"] = "sha256:" + hashlib.sha256(content).hexdigest()

    def get(self, path):
        if path == f"/git/ref/tags/{TAG}":
            self.tag_reads += 1
            return {"ref": "refs/tags/" + TAG, "object": {"type": "commit", "sha": "b" * 40 if self.moved_tag and self.tag_reads > 1 else SHA}}
        if path.startswith("/actions/runs/"):
            return copy.deepcopy(self.runs[int(path.rsplit("/", 1)[-1])])
        raise AssertionError(path)

    def list(self, path, field):
        run_id = int(path.split("/")[3])
        if field == "jobs":
            return copy.deepcopy(self.jobs[run_id])
        name = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["name"][0]
        return copy.deepcopy(self.artifacts.get((run_id, name), []))


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHub()
        self.gate = gate.ReleaseGate(self.github)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "release-assets"
        self.output.mkdir()

    def stage(self, source=None):
        return self.gate.stage(TAG, CANDIDATE, PROOF, self.output, expected_sha=SHA, source_id=source)

    def assert_rejected(self):
        with self.assertRaises(gate.GateError):
            self.stage()
        self.assertEqual([], list(self.output.iterdir()))

    def test_valid_proof_stages_exact_packages_without_forwarding_token(self):
        (self.output / "existing.zip").write_bytes(b"existing")
        self.assertEqual(SHA, self.stage(BUILD))
        for platform, name in gate.PLATFORMS.items():
            self.assertEqual(self.github.payloads[platform], (self.output / name).read_bytes())
        self.assertEqual(b"existing", (self.output / "existing.zip").read_bytes())
        self.assertTrue(all(r.get_header("Authorization") == "Bearer secret-token" for r in self.github.api.requests))
        self.assertTrue(all(r.get_header("Authorization") is None for r in self.github.blob.requests))

    def test_untrusted_run_event_repository_sha_attempt_or_workflow_rejected(self):
        for run_id, key, bad in (
            (CANDIDATE, "event", "pull_request"), (CANDIDATE, "head_sha", "b" * 40),
            (CANDIDATE, "run_attempt", 2), (CANDIDATE, "head_branch", "other"),
            (PROOF, "event", "pull_request"), (PROOF, "conclusion", "failure"),
            (PROOF, "path", ".github/workflows/other.yml"),
            (PROOF, "head_repository", {"id": 999, "full_name": "other/repo"}),
        ):
            with self.subTest(run_id=run_id, key=key):
                original = self.github.runs[run_id][key]
                self.github.runs[run_id][key] = bad
                self.assert_rejected()
                self.github.runs[run_id][key] = original

    def test_missing_failed_or_skipped_jobs_and_ui_steps_rejected(self):
        for run_id, index, field, bad in (
            (CANDIDATE, 1, "conclusion", "failure"), (PROOF, 0, "conclusion", "skipped"),
            (PROOF, 1, "head_sha", "b" * 40),
        ):
            with self.subTest(run_id=run_id, field=field):
                original = self.github.jobs[run_id][index][field]
                self.github.jobs[run_id][index][field] = bad
                self.assert_rejected()
                self.github.jobs[run_id][index][field] = original
        self.github.jobs[PROOF][1]["steps"][-1]["conclusion"] = "skipped"
        self.assert_rejected()
        self.github.jobs[PROOF][1]["steps"][-1]["conclusion"] = "success"
        self.github.jobs[PROOF].pop()
        self.assert_rejected()

    def test_each_community_ui_step_is_required(self):
        for index in (0, 1):
            for step in self.github.jobs[PROOF][index]["steps"][-3:]:
                with self.subTest(step=step["name"]):
                    step["conclusion"] = "skipped"
                    self.assert_rejected()
                    step["conclusion"] = "success"

    def test_wrong_attested_candidate_and_hash_rejected(self):
        platform = "linux-x64"
        name = f"rust-liquid-ui-attestation-{platform}"
        asset = gate.PLATFORMS[platform]
        base = {"version": 1, "platform": platform, "candidate_run_id": CANDIDATE, "candidate_sha": SHA,
                "asset_name": asset, "asset_sha256": hashlib.sha256(self.github.payloads[platform]).hexdigest()}
        for field, value in (("candidate_run_id", PROOF), ("candidate_sha", "b" * 40),
                             ("asset_name", "other.exe"), ("asset_sha256", "f" * 64), ("version", True)):
            with self.subTest(field=field):
                data = dict(base, **{field: value})
                self.github.replace(PROOF, name, zipped(f"{platform}.json", json.dumps(data)))
                self.assert_rejected()

    def test_archive_metadata_digest_size_and_members_are_enforced(self):
        platform = "linux-x64"
        name = f"rust-liquid-{platform}-candidate"
        original = copy.deepcopy(self.github.artifacts[(CANDIDATE, name)][0])
        for field, bad in (("expired", True), ("size_in_bytes", gate.MAX_PACKAGE_ARCHIVE + 1),
                           ("digest", "sha256:" + "0" * 64), ("workflow_run", {"id": PROOF})):
            with self.subTest(field=field):
                self.github.artifacts[(CANDIDATE, name)][0][field] = bad
                self.assert_rejected()
                self.github.artifacts[(CANDIDATE, name)][0] = copy.deepcopy(original)
        self.github.replace(CANDIDATE, name, zipped("../" + gate.PLATFORMS[platform], b"unsafe"))
        self.assert_rejected()
        self.github.replace(CANDIDATE, name, zipped(gate.PLATFORMS[platform], b"valid", (("extra", b"unsafe"),)))
        self.assert_rejected()

    def test_duplicate_or_missing_attestation_rejected(self):
        key = (PROOF, "rust-liquid-ui-attestation-linux-x64")
        self.github.artifacts[key].append(copy.deepcopy(self.github.artifacts[key][0]))
        self.assert_rejected()
        self.github.artifacts[key] = []
        self.assert_rejected()

    def test_duplicate_json_keys_and_unsafe_redirect_rejected(self):
        name = "rust-liquid-ui-attestation-linux-x64"
        self.github.replace(PROOF, name, zipped("linux-x64.json", b'{"version":1,"version":1}'))
        self.assert_rejected()
        self.github = FakeGitHub()
        self.gate = gate.ReleaseGate(self.github)
        self.github.api.location = "http://storage.example/1?secret=private"
        self.assert_rejected()
        self.assertEqual([], self.github.blob.requests)

    def test_moved_tag_and_collision_leave_no_partial_staging(self):
        self.github.moved_tag = True
        self.assert_rejected()
        self.github.moved_tag = False
        existing = self.output / gate.PLATFORMS["windows-x64"]
        existing.write_bytes(b"original")
        with self.assertRaises(gate.GateError):
            self.stage()
        self.assertEqual([existing], list(self.output.iterdir()))
        self.assertEqual(b"original", existing.read_bytes())

    def test_failed_release_job_is_acceptable_only_if_source_build_jobs_succeeded(self):
        self.github.runs[BUILD]["conclusion"] = "failure"
        self.gate.source_build(TAG, SHA, BUILD)
        self.github.jobs[BUILD][2]["conclusion"] = "failure"
        with self.assertRaises(gate.GateError):
            self.gate.source_build(TAG, SHA, BUILD)
        self.github.jobs[BUILD][2]["conclusion"] = "success"
        self.github.runs[BUILD]["head_branch"] = "main"
        with self.assertRaises(gate.GateError):
            self.gate.source_build(TAG, SHA, BUILD)


if __name__ == "__main__":
    unittest.main()

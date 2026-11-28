#!/usr/bin/env python3
"""Stage only Rust Liquid packages attested by trusted, successful UI runs.

The GitHub token is read from GH_TOKEN and is sent only to GITHUB_API_URL. The
signed artifact URL returned by GitHub is fetched without that token.
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


PLATFORMS = {
    "linux-x64": "GigaAMGUI-Rust-Liquid-linux-x64.AppImage",
    "windows-x64": "GigaAMGUI-Rust-Liquid-windows-x64-setup.exe",
}
MAX_ASSET = 2 * 1024 * 1024 * 1024 - 1
MAX_PACKAGE_ARCHIVE = MAX_ASSET + 16 * 1024 * 1024
MAX_ATTESTATION = 16 * 1024
MAX_ATTESTATION_ARCHIVE = 2 * 1024 * 1024
BLOCK_SIZE = 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
TAG = re.compile(r"v[0-9][A-Za-z0-9._-]{0,127}\Z", re.ASCII)
POSITIVE_ID = re.compile(r"[1-9][0-9]{0,18}\Z", re.ASCII)


class GateError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise GateError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def decode_json(data):
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(GateError("Invalid JSON constant")),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GateError("Invalid JSON") from error


def valid_id(value):
    require(isinstance(value, str) and POSITIVE_ID.fullmatch(value), "Workflow run ID must be a positive integer")
    return int(value)


def valid_tag(tag):
    require(isinstance(tag, str) and TAG.fullmatch(tag) and ".." not in tag and not tag.endswith((".", ".lock")), "Invalid release tag")
    return tag


def valid_sha(sha):
    require(isinstance(sha, str) and SHA.fullmatch(sha), "Invalid commit SHA")
    return sha


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        require(parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password, "Unsafe artifact redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHub:
    def __init__(self, repository, repository_id, api_url, token):
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or "", re.ASCII), "Invalid repository")
        self.repository_id = valid_id(repository_id)
        self.repository = repository
        parsed = urllib.parse.urlsplit(api_url or "")
        require(parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment, "Invalid GitHub API URL")
        require(isinstance(token, str) and token, "GH_TOKEN is required")
        self.base = api_url.rstrip("/") + "/repos/" + repository
        self.token = token
        self.api = urllib.request.build_opener(NoRedirect())
        self.blob = urllib.request.build_opener(HTTPSRedirect())

    def request(self, path):
        return urllib.request.Request(
            self.base + path,
            headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + self.token, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "rust-liquid-release-gate"},
        )

    def get(self, path):
        try:
            with self.api.open(self.request(path), timeout=60) as response:
                data = response.read(4 * 1024 * 1024 + 1)
            require(len(data) <= 4 * 1024 * 1024, "GitHub API response too large")
            return decode_json(data)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
            raise GateError("GitHub API request failed") from error

    def list(self, path, field):
        items = []
        for page in range(1, 12):
            separator = "&" if "?" in path else "?"
            data = self.get(f"{path}{separator}per_page=100&page={page}")
            require(isinstance(data, dict) and type(data.get("total_count")) is int and isinstance(data.get(field), list), "Invalid GitHub list response")
            require(0 <= data["total_count"] <= 1000, "GitHub list exceeds limit")
            items.extend(data[field])
            require(len(items) <= data["total_count"], "Inconsistent GitHub list response")
            if len(items) == data["total_count"]:
                return items
            require(data[field], "Incomplete GitHub list response")
        raise GateError("GitHub list pagination exceeded limit")

    def download(self, artifact, destination):
        request = self.request(f"/actions/artifacts/{artifact['id']}/zip")
        try:
            try:
                response = self.api.open(request, timeout=60)
            except urllib.error.HTTPError as error:
                if error.code != 302:
                    raise GateError("GitHub artifact request failed") from error
                response = error
            with response:
                require(response.status == 302, "Expected GitHub artifact redirect")
                location = response.headers.get("Location")
            parsed = urllib.parse.urlsplit(location or "")
            require(parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password, "Unsafe artifact URL")
            # Never forward the GitHub token to signed blob storage or redirects.
            blob_request = urllib.request.Request(location, headers={"User-Agent": "rust-liquid-release-gate"})
            digest = hashlib.sha256()
            count = 0
            with self.blob.open(blob_request, timeout=120) as blob, destination.open("xb") as output:
                require(blob.status == 200, "Artifact download failed")
                length = blob.headers.get("Content-Length")
                if length is not None:
                    require(length.isascii() and length.isdecimal() and int(length) == artifact["size_in_bytes"], "Artifact content length mismatch")
                while True:
                    block = blob.read(BLOCK_SIZE)
                    if not block:
                        break
                    count += len(block)
                    require(count <= artifact["size_in_bytes"], "Artifact exceeds declared size")
                    digest.update(block)
                    output.write(block)
            require(count == artifact["size_in_bytes"], "Artifact size mismatch")
            require(hmac.compare_digest("sha256:" + digest.hexdigest(), artifact["digest"]), "Artifact archive SHA-256 mismatch")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
            # Do not print signed URLs or tokens contained in transport errors.
            raise GateError("Artifact download failed") from error


class ReleaseGate:
    def __init__(self, github):
        self.github = github

    def tag_sha(self, tag):
        valid_tag(tag)
        ref = self.github.get("/git/ref/tags/" + urllib.parse.quote(tag, safe=""))
        require(isinstance(ref, dict) and ref.get("ref") == "refs/tags/" + tag, "Release tag not found")
        obj = ref.get("object")
        seen = set()
        for _ in range(6):
            require(isinstance(obj, dict), "Invalid release tag object")
            sha = valid_sha(obj.get("sha"))
            if obj.get("type") == "commit":
                return sha
            require(obj.get("type") == "tag" and sha not in seen, "Release tag does not point to a commit")
            seen.add(sha)
            annotated = self.github.get("/git/tags/" + sha)
            require(isinstance(annotated, dict) and annotated.get("sha") == sha, "Invalid annotated tag")
            obj = annotated.get("object")
        raise GateError("Too many annotated tag objects")

    def run(self, run_id, path, sha, events, conclusions=("success",)):
        run = self.github.get(f"/actions/runs/{run_id}")
        require(isinstance(run, dict), "Invalid workflow run")
        repo = run.get("repository") or {}
        head_repo = run.get("head_repository") or {}
        require(
            run.get("id") == run_id and run.get("path") == path
            and repo.get("id") == self.github.repository_id and repo.get("full_name") == self.github.repository
            and head_repo.get("id") == self.github.repository_id and head_repo.get("full_name") == self.github.repository
            and run.get("head_sha") == sha and run.get("event") in events
            and run.get("status") == "completed" and run.get("conclusion") in conclusions
            and run.get("run_attempt") == 1,
            f"Untrusted or unsuccessful workflow run {run_id}",
        )
        return run

    def jobs(self, run_id, sha, required):
        jobs = self.github.list(f"/actions/runs/{run_id}/attempts/1/jobs", "jobs")
        for name, steps in required.items():
            matching = [job for job in jobs if isinstance(job, dict) and job.get("name") == name]
            require(len(matching) == 1, f"Expected exactly one job: {name}")
            job = matching[0]
            require(job.get("run_id") == run_id and job.get("run_attempt") == 1 and job.get("head_sha") == sha and job.get("status") == "completed" and job.get("conclusion") == "success", f"Job did not succeed: {name}")
            job_steps = job.get("steps")
            require(isinstance(job_steps, list), f"Missing steps for {name}")
            for step_name in steps:
                matching_steps = [step for step in job_steps if isinstance(step, dict) and step.get("name") == step_name]
                require(len(matching_steps) == 1 and matching_steps[0].get("status") == "completed" and matching_steps[0].get("conclusion") == "success", f"Required step did not succeed: {step_name}")

    def source_build(self, tag, sha, run_id):
        run = self.run(run_id, ".github/workflows/build.yml", sha, ("push",), ("success", "failure"))
        require(run.get("head_branch") == tag, "Build run was not triggered by the release tag")
        self.jobs(run_id, sha, {name: () for name in (
            "TUI tests", "Windows x64", "macOS arm64", "Linux x64",
            "macOS arm64 full app bundle", "macOS x86_64 (Intel) ONNX app bundle", "Native Swift macOS arm64 app",
        )})

    def artifact(self, run_id, sha, name, limit):
        path = f"/actions/runs/{run_id}/artifacts?name=" + urllib.parse.quote(name, safe="")
        artifacts = self.github.list(path, "artifacts")
        matches = [artifact for artifact in artifacts if isinstance(artifact, dict) and artifact.get("name") == name]
        require(len(matches) == 1, f"Expected exactly one artifact: {name}")
        artifact = matches[0]
        source = artifact.get("workflow_run") or {}
        digest = artifact.get("digest")
        require(
            type(artifact.get("id")) is int and artifact["id"] > 0
            and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= limit
            and artifact.get("expired") is False
            and isinstance(digest, str) and digest.startswith("sha256:") and HASH.fullmatch(digest[7:])
            and source.get("id") == run_id and source.get("repository_id") == self.github.repository_id
            and source.get("head_repository_id") == self.github.repository_id and source.get("head_sha") == sha,
            f"Untrusted artifact metadata: {name}",
        )
        return artifact

    def stage(self, tag, candidate_id, proof_id, output_dir, expected_sha=None, source_id=None):
        sha = self.tag_sha(tag)
        if expected_sha is not None:
            require(valid_sha(expected_sha) == sha, "Release tag moved or does not match the triggering commit")
        if source_id is not None:
            self.source_build(tag, sha, source_id)
        candidate = self.run(candidate_id, ".github/workflows/rust-liquid.yml", sha, ("push", "workflow_dispatch"))
        require(candidate.get("head_branch") in ("main", "feat/rust-liquid-windows-linux"), "Untrusted candidate branch")
        self.run(proof_id, ".github/workflows/rust-liquid-ui-proof.yml", sha, ("push", "workflow_dispatch", "workflow_run"))
        self.jobs(candidate_id, sha, {
            "Build and compare pinned FFmpeg binaries": (),
            **{f"Rust Liquid {platform} candidate": () for platform in PLATFORMS},
        })
        self.jobs(proof_id, sha, {
            "Installed candidate UI (linux-x64)": (
                "Exercise Linux UI and save a real transcript", "Prepare Linux long-proof models through the installed UI",
                "Exercise Linux packaged long diarization", "Exercise both new models through the installed Linux UI",
                "Verify automatic Community-1 delivery through the installed Linux UI",
                "Verify incomplete Community-1 bundle through the installed Linux UI",
                "Exercise Community-1 through the installed Linux UI",
            ),
            "Installed candidate UI (windows-x64)": (
                "Exercise Windows UI and save a real transcript", "Prepare Windows long-proof models through the installed UI",
                "Exercise Windows packaged long diarization", "Exercise both new models through the installed Windows UI",
                "Verify automatic Community-1 delivery through the installed Windows UI",
                "Verify incomplete Community-1 bundle through the installed Windows UI",
                "Exercise Community-1 through the installed Windows UI",
            ),
        })
        output_dir = Path(output_dir)
        require(not output_dir.is_symlink(), "Release assets directory must not be a symlink")
        output_dir.mkdir(parents=True, exist_ok=True)
        require(output_dir.is_dir(), "Release assets path is not a directory")
        with tempfile.TemporaryDirectory(prefix=".rust-release-", dir=output_dir.parent) as temporary:
            temporary = Path(temporary)
            staged = []
            for platform, asset_name in PLATFORMS.items():
                attestation_artifact = self.artifact(proof_id, sha, f"rust-liquid-ui-attestation-{platform}", MAX_ATTESTATION_ARCHIVE)
                attestation_zip = temporary / f"{platform}-attestation.zip"
                self.github.download(attestation_artifact, attestation_zip)
                attestation_path = temporary / f"{platform}.json"
                extract_one(attestation_zip, f"{platform}.json", attestation_path, MAX_ATTESTATION)
                attestation = decode_json(attestation_path.read_bytes())
                require(isinstance(attestation, dict) and set(attestation) == {"version", "platform", "candidate_run_id", "candidate_sha", "asset_name", "asset_sha256"}, "Invalid attestation fields")
                require(type(attestation["version"]) is int and attestation["version"] == 1 and attestation["platform"] == platform and type(attestation["candidate_run_id"]) is int and attestation["candidate_run_id"] == candidate_id and attestation["candidate_sha"] == sha and attestation["asset_name"] == asset_name and isinstance(attestation["asset_sha256"], str) and HASH.fullmatch(attestation["asset_sha256"]), f"Attestation does not match {platform} candidate")
                package_artifact = self.artifact(candidate_id, sha, f"rust-liquid-{platform}-candidate", MAX_PACKAGE_ARCHIVE)
                package_zip = temporary / f"{platform}-candidate.zip"
                self.github.download(package_artifact, package_zip)
                package = temporary / asset_name
                actual = extract_one(package_zip, asset_name, package, MAX_ASSET)
                require(hmac.compare_digest(actual, attestation["asset_sha256"]), f"Installed package SHA-256 mismatch: {platform}")
                package_zip.unlink()
                staged.append(package)
            require(self.tag_sha(tag) == sha, "Release tag moved while validating artifacts")
            linked = []
            try:
                for package in staged:
                    destination = output_dir / package.name
                    os.link(package, destination, follow_symlinks=False)
                    linked.append(destination)
            except OSError as error:
                for destination in linked:
                    destination.unlink()
                raise GateError("Cannot stage both verified packages without overwriting existing assets") from error
        return sha


def extract_one(archive_path, expected_name, destination, max_size):
    try:
        # Bound the central directory before ZipFile allocates and parses it.
        # _EndRecData also resolves ZIP64 records written by upload-artifact.
        with archive_path.open("rb") as source:
            end = zipfile._EndRecData(source)
        require(end is not None and end[zipfile._ECD_ENTRIES_TOTAL] == 1 and 0 < end[zipfile._ECD_SIZE] <= 1024 * 1024, "Unsafe artifact ZIP directory")
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            require(len(members) == 1, "Artifact ZIP must contain exactly one file")
            member = members[0]
            kind = stat.S_IFMT(member.external_attr >> 16)
            require(member.filename == expected_name and not member.is_dir() and kind in (0, stat.S_IFREG) and not member.flag_bits & 1 and member.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) and 0 < member.file_size <= max_size, "Unsafe artifact ZIP member or size")
            digest = hashlib.sha256()
            count = 0
            with archive.open(member) as source, destination.open("xb") as output:
                while True:
                    block = source.read(BLOCK_SIZE)
                    if not block:
                        break
                    count += len(block)
                    require(count <= member.file_size and count <= max_size, "Artifact member exceeds declared size")
                    output.write(block)
                    digest.update(block)
            require(count == member.file_size, "Artifact member size mismatch")
            return digest.hexdigest()
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise GateError("Cannot extract verified artifact") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check-source", "stage", "check-tag"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-tag-sha")
    parser.add_argument("--source-build-run-id")
    parser.add_argument("--candidate-run-id")
    parser.add_argument("--ui-run-id")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    github = GitHub(os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_REPOSITORY_ID"), os.environ.get("GITHUB_API_URL"), os.environ.get("GH_TOKEN"))
    gate = ReleaseGate(github)
    if args.operation == "check-source":
        sha = gate.tag_sha(args.tag)
        gate.source_build(args.tag, sha, valid_id(args.source_build_run_id))
    elif args.operation == "check-tag":
        sha = gate.tag_sha(args.tag)
        require(sha == valid_sha(args.expected_tag_sha), "Release tag moved since verification")
    else:
        require(args.output_dir is not None, "Output directory is required")
        source_id = valid_id(args.source_build_run_id) if args.source_build_run_id is not None else None
        sha = gate.stage(args.tag, valid_id(args.candidate_run_id), valid_id(args.ui_run_id), args.output_dir, args.expected_tag_sha, source_id)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"tag_sha={sha}\n")
    print(f"Release gate {args.operation} succeeded for {args.tag} at {sha}")


if __name__ == "__main__":
    try:
        main()
    except (GateError, OSError, ValueError, TypeError) as error:
        print(f"Release gate failed: {error}", file=sys.stderr)
        raise SystemExit(1)

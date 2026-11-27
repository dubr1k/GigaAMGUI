"""Verify a downloaded export artifact and unpack its flat, checksummed model files.

The expected archive size and SHA-256 must come from the independently pinned
GitHub Actions artifact, not from the archive. No archive paths are extracted.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path


MODELS = {
    "v4": (
        "v4_rnnt",
        "ai-sage/GigaAM-v4",
        "9fe3ebf50c9e0cce5eed60d546938632238b6a96",
    ),
    "multilingual": (
        "multilingual_v2_rnnt",
        "ai-sage/GigaAM-Multilingual-v2",
        "c21483b5e185ff32f6fb2e2c801f92f4e7fbfee8",
    ),
}
MAX_METADATA = 2_000_000
MAX_PART = 1_900_000_000
MAX_PARTS = 128
MAX_FILES = 4096
MAX_TOTAL = 10_000_000_000
MAX_ARCHIVE = MAX_TOTAL + 16_000_000
RESERVE = 512 * 1024 * 1024
BLOCK = 1024 * 1024
REQUIRED = {
    "rnnt_encoder.onnx",
    "rnnt_e2e_decoder.onnx",
    "rnnt_e2e_joint.onnx",
    "rnnt_320ms_stream_frontend.onnx",
    "rnnt_320ms_stream_step.onnx",
    "rnnt_e2e_tokenizer.model",
    "streaming.json",
    "MODEL_LICENSE.txt",
    "MODEL_PROVENANCE.json",
}


def valid_name(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 255:
        return False
    if name.startswith(".") or name.endswith(".") or not re.fullmatch(r"[A-Za-z0-9._-]+", name, flags=re.ASCII):
        return False
    stem = name.split(".", 1)[0].upper()
    return stem not in {"CON", "PRN", "AUX", "NUL"} and not re.fullmatch(r"(?:COM|LPT)[1-9]", stem)


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value, flags=re.ASCII) is not None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def decode_json(data):
    if len(data) > MAX_METADATA:
        raise ValueError("JSON metadata exceeds its size limit")
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)


def validate_metadata(metadata, model):
    model_id, _, _ = MODELS[model]
    if not isinstance(metadata, dict) or type(metadata.get("version")) is not int:
        raise ValueError("invalid metadata")
    if metadata["version"] != 1 or metadata.get("model_id") != model_id:
        raise ValueError("metadata version or model id does not match")
    parts = metadata.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
        raise ValueError("invalid number of parts")

    names = set()
    total = 0
    result = []
    for index, part in enumerate(parts, 1):
        if not isinstance(part, dict):
            raise ValueError("invalid part")
        asset = f"{model_id}-part-{index:03}.bin"
        url = f"https://github.com/dubr1k/GigaAMGUI/releases/download/rust-models-v1/{asset}"
        size, digest, files = part.get("size"), part.get("sha256"), part.get("files")
        if part.get("url") != url or type(size) is not int or not 1 <= size <= MAX_PART or not valid_hash(digest):
            raise ValueError(f"invalid part metadata: {asset}")
        if not isinstance(files, list) or not files:
            raise ValueError(f"empty part: {asset}")
        part_total = 0
        for file in files:
            if not isinstance(file, dict):
                raise ValueError("invalid file entry")
            name, length, file_hash = file.get("name"), file.get("size"), file.get("sha256")
            if not valid_name(name) or name.lower() in names:
                raise ValueError(f"unsafe or duplicate model filename: {name!r}")
            names.add(name.lower())
            if len(names) > MAX_FILES or type(length) is not int or not 1 <= length <= MAX_PART or not valid_hash(file_hash):
                raise ValueError(f"invalid model file metadata: {name}")
            part_total += length
        if part_total != size:
            raise ValueError(f"part size does not match its files: {asset}")
        total += size
        if total > MAX_TOTAL:
            raise ValueError("model exceeds total size limit")
        result.append((asset, size, digest, files))
    if not {name.lower() for name in REQUIRED}.issubset(names):
        raise ValueError("required model files are missing")
    return result, total


def archive_members(archive):
    members = archive.infolist()
    if not 1 <= len(members) <= MAX_PARTS + 1:
        raise ValueError("invalid number of archive members")
    found = {}
    folded = set()
    for info in members:
        name = info.filename
        kind = stat.S_IFMT(info.external_attr >> 16)
        if (not valid_name(name) or name.lower() in folded or info.is_dir() or
                kind not in (0, stat.S_IFREG) or info.flag_bits & 1 or
                info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise ValueError(f"unsafe or duplicate archive member: {name!r}")
        folded.add(name.lower())
        found[name] = info
    return found


def unpack(model, artifact_dir, output, archive_size, archive_sha256):
    if model not in MODELS:
        raise ValueError("unknown model")
    if type(archive_size) is not int or not 1 <= archive_size <= MAX_ARCHIVE or not valid_hash(archive_sha256):
        raise ValueError("invalid expected archive size or SHA-256")
    artifact_dir, output = Path(artifact_dir), Path(output)
    if artifact_dir.is_symlink() or not artifact_dir.is_dir():
        raise ValueError("artifact directory must be a real directory")
    candidates = list(artifact_dir.iterdir())
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise ValueError("expected exactly one regular artifact archive")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    with os.fdopen(os.open(candidates[0], flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != archive_size:
            raise ValueError("artifact archive size or file type does not match")
        digest = hashlib.sha256()
        while block := stream.read(BLOCK):
            digest.update(block)
        if digest.hexdigest() != archive_sha256:
            raise ValueError("artifact archive SHA-256 does not match")
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            members = archive_members(archive)
            metadata_info = members.get("metadata.json")
            if metadata_info is None or not 1 <= metadata_info.file_size <= MAX_METADATA:
                raise ValueError("missing or oversized metadata.json")
            metadata = decode_json(archive.read(metadata_info))
            parts, total = validate_metadata(metadata, model)
            if set(members) != {"metadata.json", *(part[0] for part in parts)}:
                raise ValueError("archive members do not match metadata")
            for asset, size, _, _ in parts:
                if members[asset].file_size != size:
                    raise ValueError(f"archive part size does not match metadata: {asset}")
            if not output.parent.is_dir() or shutil.disk_usage(output.parent).free < total + RESERVE:
                raise ValueError("output parent is missing or has insufficient free space")
            output.mkdir(mode=0o700)
            try:
                for asset, _, expected_part_hash, files in parts:
                    part_hash = hashlib.sha256()
                    with archive.open(members[asset]) as part:
                        for file in files:
                            file_hash = hashlib.sha256()
                            remaining = file["size"]
                            with (output / file["name"]).open("xb") as destination:
                                while remaining:
                                    block = part.read(min(BLOCK, remaining))
                                    if not block:
                                        raise ValueError(f"truncated part: {asset}")
                                    destination.write(block)
                                    part_hash.update(block)
                                    file_hash.update(block)
                                    remaining -= len(block)
                            if file_hash.hexdigest() != file["sha256"]:
                                raise ValueError(f"model file SHA-256 does not match: {file['name']}")
                        if part.read(1):
                            raise ValueError(f"unexpected trailing bytes in part: {asset}")
                    if part_hash.hexdigest() != expected_part_hash:
                        raise ValueError(f"part SHA-256 does not match: {asset}")
                provenance_path = output / "MODEL_PROVENANCE.json"
                if provenance_path.stat().st_size > MAX_METADATA:
                    raise ValueError("model provenance exceeds its size limit")
                provenance = decode_json(provenance_path.read_bytes())
                _, repo, revision = MODELS[model]
                if not isinstance(provenance, dict) or (provenance.get("repo"), provenance.get("revision"), provenance.get("license")) != (repo, revision, "MIT"):
                    raise ValueError("model provenance does not match the pinned source")
                after = os.fstat(stream.fileno())
                if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                    raise ValueError("artifact changed while unpacking")
            except BaseException:
                shutil.rmtree(output)
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=MODELS)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("archive_size", type=int)
    parser.add_argument("archive_sha256")
    args = parser.parse_args()
    try:
        unpack(args.model, args.artifact_dir, args.output, args.archive_size, args.archive_sha256)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Cannot unpack model artifact: {error}") from error


if __name__ == "__main__":
    main()

"""Package a completed pinned export as GitHub Release parts and build metadata.

The output directory is exclusively created. Each part is the concatenation of
whole files in metadata order; a file is never split. Publish the generated .bin
assets to the requested release tag before using the JSON in a release build.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path

import onnx
from google.protobuf.descriptor import FieldDescriptor
from onnx import TensorProto

from export import MIT_LICENSE, MODELS

MAX_PART = 1_900_000_000
MAX_PARTS = 128
MAX_FILES = 4096
RESERVE = 512 * 1024 * 1024
GRAPHS = (
    "rnnt_encoder.onnx",
    "rnnt_e2e_decoder.onnx",
    "rnnt_e2e_joint.onnx",
    "rnnt_320ms_stream_frontend.onnx",
    "rnnt_320ms_stream_step.onnx",
)
REQUIRED = (*GRAPHS, "streaming.json", "rnnt_e2e_tokenizer.model", "MODEL_LICENSE.txt", "MODEL_PROVENANCE.json")


def valid_name(name):
    if not 1 <= len(name) <= 255 or name.startswith(".") or name.endswith("."):
        return False
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name, flags=re.ASCII):
        return False
    stem = name.split(".", 1)[0].upper()
    return stem not in {"CON", "PRN", "AUX", "NUL"} and not re.fullmatch(r"(?:COM|LPT)[1-9]", stem)


def external_locations(message):
    if message.DESCRIPTOR.full_name == "onnx.TensorProto":
        pairs = [(item.key, item.value) for item in message.external_data]
        info = dict(pairs)
        if len(info) != len(pairs):
            raise ValueError("duplicate external tensor metadata")
        if message.data_location == TensorProto.EXTERNAL:
            name = info.get("location", "")
            if not valid_name(name) or info.get("basepath"):
                raise ValueError(f"unsafe external tensor location: {name!r}")
            yield name
        elif info:
            raise ValueError("unexpected external tensor metadata")
    for field, value in message.ListFields():
        if field.type == FieldDescriptor.TYPE_MESSAGE:
            for child in value if field.is_repeated else (value,):
                yield from external_locations(child)


def source_file(directory, name):
    if not valid_name(name):
        raise ValueError(f"invalid file name: {name!r}")
    path = directory / name
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_PART:
        raise ValueError(f"invalid size or type for {name}")
    return path, info


def collect_files(directory, model):
    repo, revision, _ = MODELS[model]
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("source must be a regular export directory")
    license_path, _ = source_file(directory, "MODEL_LICENSE.txt")
    provenance_path, _ = source_file(directory, "MODEL_PROVENANCE.json")
    if license_path.read_text(encoding="utf-8").strip() != MIT_LICENSE:
        raise ValueError("unexpected model licence")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if (provenance.get("repo"), provenance.get("revision"), provenance.get("license")) != (repo, revision, "MIT"):
        raise ValueError("export provenance does not match the pinned source")
    names = set(REQUIRED)
    for name in GRAPHS:
        path, _ = source_file(directory, name)
        graph = onnx.load(path, load_external_data=False)
        names.update(external_locations(graph))
    config_path, _ = source_file(directory, "streaming.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("frontend", {}).get("file") != GRAPHS[3] or config.get("step", {}).get("file") != GRAPHS[4]:
        raise ValueError("streaming configuration refers to unexpected graphs")
    ordered = sorted(names)
    if len(ordered) > MAX_FILES or len({name.lower() for name in ordered}) != len(ordered):
        raise ValueError("too many files or case-insensitive duplicate names")
    return [source_file(directory, name) for name in ordered]


def write_part(path, files):
    part_hash = hashlib.sha256()
    entries = []
    with path.open("xb") as part:
        for source, before in files:
            file_hash = hashlib.sha256()
            length = 0
            with source.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                    before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
                ):
                    raise ValueError(f"source changed: {source.name}")
                while block := stream.read(1024 * 1024):
                    part.write(block)
                    part_hash.update(block)
                    file_hash.update(block)
                    length += len(block)
                after = os.fstat(stream.fileno())
                if length != before.st_size or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                    raise ValueError(f"source changed while packaging: {source.name}")
            entries.append({"name": source.name, "size": length, "sha256": file_hash.hexdigest()})
        part.flush()
        os.fsync(part.fileno())
    return {"size": sum(entry["size"] for entry in entries), "sha256": part_hash.hexdigest(), "files": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=MODELS)
    parser.add_argument("source", type=Path, help="OUTPUT/model from export.py")
    parser.add_argument("output", type=Path)
    parser.add_argument("--tag", required=True, help="future public GitHub release tag")
    args = parser.parse_args()
    if not valid_name(args.tag):
        parser.error("tag must be a flat ASCII GitHub release tag")
    if not args.source.is_absolute() or not args.output.is_absolute() or not args.output.parent.is_dir() or args.output.exists():
        parser.error("source and new output must be absolute paths")
    files = collect_files(args.source, args.model)
    required = sum(info.st_size for _, info in files) + RESERVE
    if shutil.disk_usage(args.output.parent).free < required:
        parser.error(f"at least {required} free bytes are required to package the export")
    groups, group, total = [], [], 0
    for item in files:
        if group and total + item[1].st_size > MAX_PART:
            groups.append(group)
            group, total = [], 0
        group.append(item)
        total += item[1].st_size
    if group:
        groups.append(group)
    if not groups or len(groups) > MAX_PARTS:
        parser.error("export requires an unsupported number of release parts")
    model_id = "v4_rnnt" if args.model == "v4" else "multilingual_v2_rnnt"
    args.output.mkdir(exist_ok=False)
    try:
        stage = args.output / ".staging"
        stage.mkdir()
        parts = []
        for index, group in enumerate(groups, 1):
            asset = f"{model_id}-part-{index:03}.bin"
            result = write_part(stage / asset, group)
            result["url"] = f"https://github.com/dubr1k/GigaAMGUI/releases/download/{args.tag}/{asset}"
            parts.append(result)
        metadata = {"version": 1, "model_id": model_id, "parts": parts}
        with (stage / "metadata.json").open("x", encoding="utf-8", newline="\n") as output:
            json.dump(metadata, output, ensure_ascii=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        stage.rename(args.output / "release")
    except BaseException:
        shutil.rmtree(args.output)
        raise


if __name__ == "__main__":
    main()

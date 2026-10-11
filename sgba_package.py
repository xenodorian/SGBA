"""Validation and extraction for the versioned .sgba game package format."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

FORMAT_NAME = "sgba"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_FILES = 2000
MAX_MANIFEST_BYTES = 64 * 1024
MANIFEST_NAME = "manifest.json"


class PackageError(ValueError):
    """A .sgba package is invalid or exceeds a safety limit."""


def _safe_member_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise PackageError(f"Unsafe package path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise PackageError(f"Unsafe package path: {name!r}")
    if path.parts and ":" in path.parts[0]:
        raise PackageError(f"Unsafe package path: {name!r}")
    return path.as_posix()


def _read_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        info = archive.getinfo(MANIFEST_NAME)
    except KeyError as exc:
        raise PackageError("Package is missing root manifest.json") from exc
    if info.file_size > MAX_MANIFEST_BYTES:
        raise PackageError("manifest.json exceeds 64 KiB")
    try:
        manifest = json.loads(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, zipfile.BadZipFile) as exc:
        raise PackageError(f"Cannot read package manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PackageError("manifest.json must contain a JSON object")
    allowed = {"format", "format_version", "title", "entry_point"}
    unknown = set(manifest) - allowed
    if unknown:
        raise PackageError(f"Unsupported manifest fields: {', '.join(sorted(unknown))}")
    if manifest.get("format") != FORMAT_NAME:
        raise PackageError("manifest format must be 'sgba'")
    if type(manifest.get("format_version")) is not int or manifest["format_version"] != FORMAT_VERSION:
        raise PackageError(f"Unsupported SGBA package format version: {manifest.get('format_version')!r}")
    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 80:
        raise PackageError("manifest title must contain 1 to 80 characters")
    entry_point = manifest.get("entry_point")
    if not isinstance(entry_point, str):
        raise PackageError("manifest entry_point must be a relative Python file path")
    entry_point = _safe_member_name(entry_point)
    if not entry_point.endswith(".py"):
        raise PackageError("manifest entry_point must end in .py")
    manifest["entry_point"] = entry_point
    return manifest


def validate_package(path: str | Path) -> dict:
    """Validate a .sgba archive and return its manifest without extracting it."""
    package_path = Path(path)
    if package_path.suffix.lower() != ".sgba":
        raise PackageError("Game package must use the .sgba extension")
    try:
        archive_size = package_path.stat().st_size
    except OSError as exc:
        raise PackageError(f"Cannot access package: {exc}") from exc
    if archive_size > MAX_ARCHIVE_BYTES:
        raise PackageError("Package exceeds the 32 MiB compressed-size limit")
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES:
                raise PackageError(f"Package contains more than {MAX_FILES} files")
            seen = set()
            total_size = 0
            for info in infos:
                name = _safe_member_name(info.filename.rstrip("/"))
                if name in seen:
                    raise PackageError(f"Duplicate package path: {name}")
                seen.add(name)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise PackageError(f"Symbolic links are not allowed: {name}")
                if info.flag_bits & 0x1:
                    raise PackageError(f"Encrypted files are not supported: {name}")
                if info.file_size > MAX_FILE_BYTES:
                    raise PackageError(f"Package file exceeds 16 MiB: {name}")
                total_size += info.file_size
                if total_size > MAX_UNPACKED_BYTES:
                    raise PackageError("Package exceeds the 64 MiB unpacked-size limit")
                if info.file_size > 1024 * 1024 and info.compress_size > 0:
                    if info.file_size / info.compress_size > 200:
                        raise PackageError(f"Suspicious compression ratio: {name}")
            manifest = _read_manifest(archive)
            if manifest["entry_point"] not in seen:
                raise PackageError(f"Package entry point is missing: {manifest['entry_point']}")
            if MANIFEST_NAME not in seen:
                raise PackageError("Package is missing root manifest.json")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise PackageError(f"Corrupt package member: {bad_member}")
            return manifest
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError(f"Cannot read .sgba package: {exc}") from exc


def resolve_game_path(path: str | Path) -> Path:
    """Return a Python entry-point path, extracting validated .sgba files to a cache."""
    game_path = Path(path).expanduser().resolve()
    if game_path.suffix.lower() != ".sgba":
        if not game_path.is_file():
            raise FileNotFoundError(f"Game file not found: {game_path}")
        return game_path
    manifest = validate_package(game_path)
    digest = hashlib.sha256(game_path.read_bytes()).hexdigest()
    cache_root = Path(tempfile.gettempdir()) / "sgba-package-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / digest
    entry_path = target.joinpath(*PurePosixPath(manifest["entry_point"]).parts)
    if entry_path.is_file():
        return entry_path

    staging = cache_root / (digest + ".extracting")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        with zipfile.ZipFile(game_path, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = _safe_member_name(info.filename)
                destination = staging.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=64 * 1024)
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    if not entry_path.is_file():
        raise PackageError(f"Package entry point was not extracted: {manifest['entry_point']}")
    return entry_path

#!/usr/bin/env python3
import hashlib
import os
import shutil
import sys
from pathlib import Path


MANIFEST = ".pstack-install.sha256"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        value, sep, name = line.partition("  ")
        if sep:
            entries[name] = value
    return entries


def write_manifest(path: Path, entries: dict[str, str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        "".join(f"{value}  {name}\n" for name, value in sorted(entries.items())),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def remove(target: Path, dst: Path) -> None:
    target.unlink()
    parent = target.parent
    while parent != dst and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def install(src: Path, dst: Path, subdirs: list[str]) -> None:
    manifest_path = dst / MANIFEST
    previous = read_manifest(manifest_path)
    current: dict[str, str] = {}
    installed_hashes: dict[str, str] = {}
    changes = []
    dst.mkdir(parents=True, exist_ok=True)
    for subdir in subdirs:
        source = src / subdir
        if not source.is_dir():
            continue
        for original in sorted(source.rglob("*")):
            if not original.is_file() or {"node_modules", "__pycache__"}.intersection(
                original.parts
            ):
                continue
            rel = original.relative_to(src).as_posix()
            target = dst / rel
            new_hash = digest(original)
            current[rel] = new_hash
            old_hash = previous.get(rel)
            target_hash = digest(target) if target.is_file() else None
            if target_hash == new_hash:
                installed_hashes[rel] = new_hash
                continue
            if old_hash is not None and target_hash == old_hash:
                pass
            elif old_hash is not None or target.exists():
                sidecar = target.with_name(target.name + ".pstack-new")
                if not sidecar.is_file() or digest(sidecar) != new_hash:
                    sidecar.parent.mkdir(parents=True, exist_ok=True)
                    tmp = sidecar.with_name(sidecar.name + ".tmp")
                    shutil.copy2(original, tmp)
                    os.replace(tmp, sidecar)
                    changes.append(f"kept local edit {target}; wrote {sidecar}")
                installed_hashes[rel] = old_hash or new_hash
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".pstack-tmp")
            shutil.copy2(original, tmp)
            os.replace(tmp, target)
            changes.append(f"installed {target}")
            installed_hashes[rel] = new_hash

    for rel, old_hash in previous.items():
        if rel in current:
            continue
        target = dst / rel
        if target.is_file() and digest(target) == old_hash:
            remove(target, dst)
            changes.append(f"removed {target}")
        elif target.exists():
            changes.append(f"kept local edit for removed source file {target}")
    write_manifest(manifest_path, installed_hashes)
    if changes:
        for change in changes:
            print(change)


def uninstall(dst: Path) -> None:
    manifest_path = dst / MANIFEST
    previous = read_manifest(manifest_path)
    for rel, old_hash in sorted(previous.items()):
        target = dst / rel
        if target.is_file() and digest(target) == old_hash:
            remove(target, dst)
            print(f"removed {target}")
        elif target.exists():
            print(f"kept local edit {target}")
    manifest_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--uninstall":
        uninstall(Path(sys.argv[2]))
    elif len(sys.argv) >= 4:
        install(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:])
    else:
        sys.exit("usage: install_tree.py SRC DST SUBDIR... | --uninstall DST")

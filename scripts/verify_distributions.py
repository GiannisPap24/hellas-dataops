"""Verify local sdist/wheel content without extracting or executing archive files.

Usage: .venv/Scripts/python.exe -I -B scripts/verify_distributions.py dist/milestone-2c
Run from this repository root after building; uses only the standard library.
"""

import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = (root / sys.argv[1]).resolve()
    if not output.is_relative_to(root):
        raise RuntimeError("Distribution directory must be inside this repository")
    sdists = list(output.glob("*.tar.gz"))
    wheels = list(output.glob("*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        raise RuntimeError("Expected exactly one source distribution and one wheel")

    required = {
        Path(name)
        for name in (
            "AGENTS.md",
            "README.md",
            "pyproject.toml",
            "MANIFEST.in",
            "uv.lock",
            "alembic.ini",
            "compose.postgres-test.yaml",
            ".env.example",
            ".python-version",
        )
    }
    for directory, patterns in (
        ("src/hellas_dataops", ("*.py", "*.mako")),
        ("docs", ("*.md",)),
        ("tests", ("*.py", "*.md", "*.json")),
        ("scripts", ("*.py", "*.ps1")),
    ):
        for pattern in patterns:
            required.update(
                path.relative_to(root) for path in (root / directory).rglob(pattern)
            )

    forbidden = {
        ".git",
        ".venv",
        ".tools",
        ".uv-cache",
        "__pycache__",
        "artifacts",
        "dist",
        "data",
        ".env",
    }

    def check_paths(names: list[str]) -> None:
        for name in names:
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or forbidden.intersection(path.parts)
            ):
                raise RuntimeError(f"Unexpected archive path: {name}")
            if path.suffix in {".pyc", ".pem", ".key", ".db", ".sqlite"}:
                raise RuntimeError(f"Unexpected generated/private file: {name}")

    with tarfile.open(sdists[0], "r:gz") as archive:
        members = {
            member.name: member for member in archive.getmembers() if member.isfile()
        }
        check_paths(list(members))
        prefixes = {PurePosixPath(name).parts[0] for name in members}
        if len(prefixes) != 1:
            raise RuntimeError("Expected a single source archive root")
        prefix = prefixes.pop()
        for relative in sorted(required):
            name = f"{prefix}/{relative.as_posix()}"
            if name not in members:
                raise RuntimeError(f"Missing source asset: {relative}")
            with archive.extractfile(members[name]) as stream:
                if stream.read() != (root / relative).read_bytes():
                    raise RuntimeError(f"Stale source asset: {relative}")

    source_files = sorted((root / "src/hellas_dataops").rglob("*.py"))
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        check_paths(names)
        for source in source_files:
            name = source.relative_to(root / "src").as_posix()
            if archive.read(name) != source.read_bytes():
                raise RuntimeError(f"Stale wheel module: {name}")
        template = root / "src/hellas_dataops/storage/migrations/script.py.mako"
        if (
            archive.read(template.relative_to(root / "src").as_posix())
            != template.read_bytes()
        ):
            raise RuntimeError("Missing or stale packaged Alembic template")
        if any(name.startswith(("tests/", "docs/")) for name in names):
            raise RuntimeError(
                "Tests and documentation belong in the sdist, not runtime wheel"
            )
        (metadata_name,) = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if b"# Hellas DataOps" not in archive.read(metadata_name):
            raise RuntimeError("Wheel metadata must contain the project README")

    json_count = sum(path.suffix == ".json" for path in required)
    docs_count = sum(path.parts[0] == "docs" for path in required)
    print(
        f"Verified {len(required)} source assets byte-for-byte, including {json_count} JSON fixtures and {docs_count} documentation files"
    )
    print(
        f"Verified {len(source_files)} wheel modules and Alembic template byte-for-byte; README present in wheel metadata"
    )
    print("No forbidden cache, environment, raw-data, or credential paths found")


if __name__ == "__main__":
    main()

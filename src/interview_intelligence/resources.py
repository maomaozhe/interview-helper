"""Find versioned application data in the source tree or installed wheel."""

from pathlib import Path


def resource_path(relative_path: str) -> Path:
    package = Path(__file__).resolve().parent / "resources" / relative_path
    if package.is_file():
        return package
    source = Path(__file__).resolve().parents[2] / relative_path
    if source.is_file():
        return source
    raise FileNotFoundError(f"application resource missing: {relative_path}")

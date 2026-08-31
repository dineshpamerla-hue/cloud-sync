from .base import Source, SourceFile
from .local import LocalSource
from .google_drive import GoogleDriveSource

REGISTRY = {
    "local": LocalSource,
    "google_drive": GoogleDriveSource,
}


def build_source(type_: str, options: dict):
    """Factory: turn a jobs.yaml source.type + options into a Source instance.

    Adding a new source (Dropbox, S3, iCloud, ...) means writing one adapter
    class implementing the Source ABC and adding one line to REGISTRY.
    """
    if type_ not in REGISTRY:
        raise ValueError(f"unknown source type '{type_}'. Known: {sorted(REGISTRY)}")
    return REGISTRY[type_](**options)


__all__ = ["Source", "SourceFile", "LocalSource", "GoogleDriveSource", "build_source", "REGISTRY"]

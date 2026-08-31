from .base import Destination
from .backblaze_b2 import BackblazeB2Destination

REGISTRY = {
    "backblaze_b2": BackblazeB2Destination,
}


def build_destination(type_: str, options: dict):
    """Factory mirroring sources.build_source — add a new destination (S3, etc.)
    by writing one adapter class and adding it here.
    """
    if type_ not in REGISTRY:
        raise ValueError(f"unknown destination type '{type_}'. Known: {sorted(REGISTRY)}")
    return REGISTRY[type_](**options)


__all__ = ["Destination", "BackblazeB2Destination", "build_destination", "REGISTRY"]

"""sijl: summarise your own git work for a review period, from your local repos."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sijl")
except PackageNotFoundError:  # a source tree that was never installed (uv sync installs it)
    __version__ = "0+unknown"

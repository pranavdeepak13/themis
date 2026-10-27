"""Themis: a local manager for multiple Claude Code profiles."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("themis")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.1.0"

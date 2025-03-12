try:
    from ._dist_ver import __version__  # type: ignore
except ImportError:
    try:
        from setuptools_scm import get_version  # type: ignore

        __version__ = get_version(root="..", relative_to=__file__)  # type: ignore
    except (ImportError, LookupError):
        __version__ = "UNKNOWN"

__all__ = ["__version__"]

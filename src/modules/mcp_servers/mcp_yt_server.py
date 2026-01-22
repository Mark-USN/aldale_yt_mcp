"""FastMCP server bootstrap for the YouTube MCP module.

This module wires up tool and prompt discovery/registration, and provides a CLI
for running a FastMCP HTTP server.

Agent-friendly notes:
- Registration functions are parameterized (no hidden globals required).
- Cache directory resolution is centralized and overrideable via env var.

Environment variables:
- MCP_CACHE_DIR: optional. If set, this directory is used as the cache root.

Original file: demo_server.py (2025-11-01 MMH).
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from importlib.metadata import version

from fastmcp import FastMCP

from ..utils.logging_config import setup_logging
from ..utils.prompt_loader import register_prompts
from ..utils.prompt_md_loader import register_prompts_from_markdown
from ..utils.tool_loader import register_tools

logger = logging.getLogger(__name__)

# __version__ = version("YouTube_Transcript_FastMCP_Server")

@dataclass(frozen=True, slots=True)
class ServerPaths:
    """Resolved directory layout for this MCP module.

    Attributes:
        modules_dir: Directory containing the MCP package modules.
        tools_dir: Directory containing tool packages.
        prompts_dir: Directory containing prompt packages.
        resources_dir: Directory containing resource packages.
        project_dir: Project root directory.
        cache_dir: Cache root directory.
    """

    modules_dir: Path
    tools_dir: Path
    prompts_dir: Path
    resources_dir: Path
    project_dir: Path
    cache_dir: Path


def resolve_cache_dir(project_dir: Path) -> Path:
    """Resolve the cache directory.

    Resolution order:
    1) MCP_CACHE_DIR environment variable (if set and non-empty)
    2) <project_dir>/Cache

    Args:
        project_dir: Project root directory.

    Returns:
        Absolute path to the cache root.
    """

    if override := os.environ.get("MCP_CACHE_DIR"):
        return Path(override).expanduser().resolve()
    return (project_dir / "Cache").resolve()


def resolve_paths(this_file: Path) -> ServerPaths:
    """Resolve all directories used by the server.

    Args:
        this_file: Typically ``Path(__file__)``.

    Returns:
        A :class:`ServerPaths` instance with absolute paths.
    """

    modules_dir = this_file.parents[1].resolve()
    tools_dir = (modules_dir / "tools").resolve()
    prompts_dir = (modules_dir / "prompts").resolve()
    resources_dir = (modules_dir / "resources").resolve()
    project_dir = modules_dir.parents[1].resolve()
    cache_dir = resolve_cache_dir(project_dir)
    return ServerPaths(
        modules_dir=modules_dir,
        tools_dir=tools_dir,
        prompts_dir=prompts_dir,
        resources_dir=resources_dir,
        project_dir=project_dir,
        cache_dir=cache_dir,
    )


def create_server() -> FastMCP:
    """Create the FastMCP server instance.

    Returns:
        Configured :class:`FastMCP` server.
    """

    return FastMCP(
        name="mcp_yt_server",
        include_tags={"public", "api"},
        exclude_tags={"internal", "deprecated"},
        on_duplicate_tools="error",
        on_duplicate_resources="warn",
        on_duplicate_prompts="replace",
        include_fastmcp_meta=False,
    )


def purge_cache(cache_dir: Path, *, days: int = 7) -> None:
    """Delete cache files older than ``days``.

    This is best-effort cleanup; failures are logged and ignored.

    Notes:
        - We use mtime (modification time) rather than atime, because atime may be
          disabled or unreliable on some filesystems.

    Args:
        cache_dir: Cache root directory.
        days: Number of days to keep cache files.
    """
    # Any files in audio are either currently being processed or are artifacts 
    # of prior processing that should have been deleted by the server. Although 
    # we could delete old audio files now, it's safer to leave them alone to avoid
    # interfering with in-progress operations.
    # Transcripts are derived from audio.  They are cached for n days (default 7)
    # to avoid re-downloading/re-processing if the same audio is requested again.

    cutoff = time.time() - (days * 86_400)

    for rel in ("audio", "transcripts"):
        d = cache_dir / rel
        if not d.exists():
            continue

        for path in d.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed purging cache file: %s", path)


def attach_everything(*, mcp: FastMCP, paths: ServerPaths) -> None:
    """Register tools and prompts with the server.

    Warning:
        FastMCP will import code from discovered packages. Errors inside a tool
        or prompt package can prevent registration of that package.

    Args:
        mcp: Server instance to register into.
        paths: Resolved server paths.
    """

    register_tools(mcp, package=paths.tools_dir)
    logger.info("✅ Tools registered.")

    register_prompts_from_markdown(mcp, prompts_dir=paths.prompts_dir)
    logger.info("✅ Markdown prompts registered.")

    register_prompts(mcp, prompts_dir=paths.prompts_dir)
    logger.info("✅ Prompt functions registered.")


def launch_server(*, host: str = "127.0.0.1", port: int = 8085, purge_days: int | None = 7) -> None:
    """Start the FastMCP HTTP server.

    Args:
        host: Bind address.
        port: TCP port.
        purge_days: If not None, purge cache entries older than this many days
            before starting.
    """

    paths = resolve_paths(Path(__file__))
    mcp = create_server()

    if purge_days is not None:
        purge_cache(paths.cache_dir, days=purge_days)

    logger.info("✅ demo_server starting (host=%s port=%s)", host, port)
    attach_everything(mcp=mcp, paths=paths)

    # Note: mcp.run() is blocking.
    mcp.run(transport="http", host=host, port=port)


def port_type(value: str) -> int:
    """Argparse type that validates a TCP port number."""

    try:
        port = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Port must be an integer (got {value!r})") from e

    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError(f"Port number must be between 1 and 65535 (got {port})")

    return port


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""

    parser = argparse.ArgumentParser(description="Create and run an MCP server.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host name or IP address (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=port_type,
        default=8085,
        help="TCP port to bind (default: 8085).",
    )
    parser.add_argument(
        "--purge-cache-days",
        type=int,
        default=7,
        help="Purge cache files older than N days before startup (default: 7). Use -1 to disable.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""

    parser = build_parser()
    args = parser.parse_args(argv)

    purge_days = None if args.purge_cache_days < 0 else args.purge_cache_days
    launch_server(host=args.host, port=args.port, purge_days=purge_days)


if __name__ == "__main__":
    setup_logging()
    main()

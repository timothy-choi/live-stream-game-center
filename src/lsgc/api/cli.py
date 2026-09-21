"""Developer CLI for the read-only game API.

    python -m lsgc.api

Does not start tracking unless LSGC_RUN_ORCHESTRATOR=true.
"""

from __future__ import annotations

import argparse
import os

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.api",
        description=(
            "Serve the read-only game API. Tracking is backend-owned; "
            "HTTP/SSE clients never start workers."
        ),
    )
    parser.add_argument("--host", default=os.environ.get("LSGC_API_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("LSGC_API_PORT", "8000")),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    uvicorn.run(
        "lsgc.api.app:app_from_env",
        factory=True,
        host=args.host,
        port=args.port,
    )
    return 0

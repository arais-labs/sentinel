from __future__ import annotations

import argparse

import uvicorn
from app.main import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel desktop backend")
    parser.add_argument("--uds", required=True)
    args = parser.parse_args()
    uvicorn.run(app, uds=args.uds, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()

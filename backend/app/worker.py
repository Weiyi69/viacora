import argparse
import logging

from app.services.database import get_session_factory
from app.services.document_worker import run_worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued Viacora PDF documents.")
    parser.add_argument("--once", action="store_true", help="Process at most one queued document.")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds to wait when no eligible document is available.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    run_worker(
        get_session_factory(),
        poll_interval_seconds=max(0.1, args.poll_interval),
        run_once=args.once,
    )


if __name__ == "__main__":
    main()

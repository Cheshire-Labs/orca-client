"""Main entry point for the Orca Client CLI."""

import asyncio
import argparse
import sys
import logging
from pathlib import Path

from . import __version__
from .config import setup_logging
from .session import ClientSession

logger = logging.getLogger("orca_client")


async def async_main(args: argparse.Namespace) -> int:
    """Run the client until it stops.

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    try:
        session = ClientSession.from_config_path(args.config)
        await session.run()
        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = argparse.ArgumentParser(
        description="Orca Client - connect lab devices to an Orca runtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default config (./config.json)
  python -m orca_client

  # Run with custom config file
  python -m orca_client --config /path/to/config.json

  # Run with verbose logging
  python -m orca_client --verbose

Configuration:
  Create a config.json file with platform and device settings.
  See README.md and examples/config.example.json for details.
        """
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=Path("config.json"),
        help="Path to config.json file (default: ./config.json)"
    )
    parser.add_argument(
        "--env", "-e",
        type=Path,
        default=None,
        help="Path to .env file to load (default: use OS environment variables)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}"
    )

    args = parser.parse_args()

    # Load .env file if specified (before any config loading)
    if args.env:
        try:
            from dotenv import load_dotenv
            load_dotenv(args.env, override=True)
            print(f"Loaded environment from: {args.env}")
        except ImportError:
            print("Warning: python-dotenv not installed, --env flag ignored")

    # Setup logging
    setup_logging(verbose=args.verbose)

    # Log startup banner
    logger.info("=" * 60)
    logger.info(f"Orca Client v{__version__}")
    logger.info("Connecting lab devices to an Orca runtime")
    logger.info("=" * 60)

    # Run async main
    try:
        exit_code = asyncio.run(async_main(args))
        return exit_code
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        return 0


if __name__ == "__main__":
    sys.exit(main())

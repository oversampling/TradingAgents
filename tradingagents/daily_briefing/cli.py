"""Non-interactive command line entrypoint for launchd."""

import argparse
import json
import sys

from .config import DailySettings
from .pipeline import DailyBriefingPipeline
from .providers.moomoo import MoomooReadOnlyProvider
from .symbol_mapper import merge_securities


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tradingagents-daily")
    parser.add_argument("--env-file", default=".env")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--no-send", action="store_true")
    run.add_argument("--force", action="store_true")
    resend = commands.add_parser("send-only")
    resend.add_argument("run_id", type=int)
    commands.add_parser("doctor")
    commands.add_parser("symbols")
    args = parser.parse_args(argv)
    settings = DailySettings.load(args.env_file)
    if args.command == "run":
        output = DailyBriefingPipeline(settings).run(send_email=not args.no_send, force=args.force)
    elif args.command == "send-only":
        output = DailyBriefingPipeline(settings).send_only(args.run_id)
    else:
        provider = MoomooReadOnlyProvider(settings.moomoo_host, settings.moomoo_port, settings.moomoo_account_id)
        watchlist = provider.watchlist(settings.watchlist_group)
        if args.command == "doctor":
            output = {"status": "ok", "watchlist_group": settings.watchlist_group, "watchlist_count": len(watchlist), "runtime_dir": str(settings.runtime_dir)}
        else:
            positions = provider.positions() if settings.include_positions else []
            symbols, skipped = merge_securities(watchlist, positions, settings.max_symbols)
            output = {"symbols": [item.__dict__ for item in symbols], "skipped": skipped}
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

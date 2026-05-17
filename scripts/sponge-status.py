#!/usr/bin/env python
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sponge_client import get_sponge_balances  # noqa: E402
from sponge_client import SpongePaymentError  # noqa: E402


def main() -> None:
    try:
        print(json.dumps({"balances": get_sponge_balances()}, indent=2, sort_keys=True))
    except SpongePaymentError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()

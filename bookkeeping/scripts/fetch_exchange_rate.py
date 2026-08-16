#!/usr/bin/env python3
"""
Fetch Exchange Rate — Frankfurter API (ECB daily rates)

Pure utility script: no BOOKKEEPING_CONFIG_PATH needed.
Usable as CLI or as importable library.

CLI:
    python3 fetch_exchange_rate.py --date 2024-01-15 --currency CAD
    python3 fetch_exchange_rate.py --date 2024-01-15 --currency CAD --rate 1.3426  # manual override

Library:
    from fetch_exchange_rate import get_rate
    rate_info = get_rate("2024-01-15", "CAD")
    # → {"success": True, "date": "2024-01-15", "currency": "CAD", "rate": 1.3426}
"""

import argparse
import json
import sys
import urllib.request
import urllib.error


FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"


def get_rate(date: str, currency: str, manual_rate: float = None) -> dict:
    """
    Get exchange rate for a given date and currency.

    Convention: 1 USD = X foreign (e.g., 1 USD = 1.3426 CAD).
    Handles weekends/holidays — API returns previous business day rate.

    Args:
        date: ISO date string (YYYY-MM-DD)
        currency: ISO 4217 currency code (e.g., "CAD", "MXN")
        manual_rate: If provided, skip API call and use this rate

    Returns:
        dict with keys: success, date, currency, rate
    Raises:
        RuntimeError on API failure
    """
    currency = currency.upper()

    if manual_rate is not None:
        if manual_rate <= 0:
            raise ValueError(f"Manual rate must be > 0, got {manual_rate}")
        return {
            "success": True,
            "date": date,
            "currency": currency,
            "rate": manual_rate,
            "source": "manual",
        }

    url = f"{FRANKFURTER_BASE}/{date}?base=USD&symbols={currency}"
    print(f"Fetching rate: {url}", file=sys.stderr)

    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "bookkeeping/1.0")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Frankfurter API HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Frankfurter API connection error: {e.reason}")

    rate = data.get("rates", {}).get(currency)
    if rate is None:
        raise RuntimeError(
            f"Currency {currency} not found in API response for {date}. "
            f"Response: {json.dumps(data)}"
        )

    # API may return a different date for weekends/holidays
    actual_date = data.get("date", date)
    if actual_date != date:
        print(
            f"  Note: {date} is not a business day, using rate from {actual_date}",
            file=sys.stderr,
        )

    return {
        "success": True,
        "date": actual_date,
        "currency": currency,
        "rate": float(rate),
        "source": "frankfurter",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Fetch exchange rate from Frankfurter API (ECB daily rates)"
    )
    parser.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    parser.add_argument("--currency", required=True, help="ISO 4217 currency code (e.g., CAD, MXN)")
    parser.add_argument("--rate", type=float, default=None,
                        help="Manual rate override (bypasses API)")
    args = parser.parse_args()

    try:
        result = get_rate(args.date, args.currency, manual_rate=args.rate)
        print(json.dumps(result, indent=2))
    except (RuntimeError, ValueError) as e:
        print(json.dumps({"success": False, "error": str(e)}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()

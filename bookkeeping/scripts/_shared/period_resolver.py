#!/usr/bin/env python3
"""
Period Resolver — shared utility for resolving period input to explicit date ranges.

Supports three period types:
  - calendar-monthly: YYYY-MM → first/last day of month
  - date-range: YYYY-MM-DD_YYYY-MM-DD → explicit start/end
  - fiscal: YYYY-PNN → lookup from fiscal calendar YAML

Called by workflow orchestrators to resolve user input before passing to scripts.
Scripts receive pre-resolved --start_date/--end_date and are period-type-agnostic.
"""

import calendar
import re
from datetime import datetime

import yaml


def resolve_period(period_input, period_type=None, fiscal_calendar_path=None):
    """Resolve period input string to an explicit date range.

    Args:
        period_input: Period string in one of the supported formats.
        period_type: One of 'calendar-monthly', 'date-range', 'fiscal', or None for auto-detect.
        fiscal_calendar_path: Path to fiscal-calendar.yaml (required for fiscal type).

    Returns:
        dict: {periodLabel: str, periodStart: str, periodEnd: str, periodType: str}
              All dates as YYYY-MM-DD strings.

    Raises:
        ValueError: On invalid input, missing config, or lookup failure.
    """
    if not period_input or not period_input.strip():
        raise ValueError("Period input cannot be empty")

    period_input = period_input.strip()

    # Auto-detect period type if not specified
    if period_type is None:
        period_type = _detect_period_type(period_input)

    if period_type == 'calendar-monthly':
        return _resolve_calendar_monthly(period_input)
    elif period_type == 'date-range':
        return _resolve_date_range(period_input)
    elif period_type == 'fiscal':
        return _resolve_fiscal(period_input, fiscal_calendar_path)
    else:
        raise ValueError(f"Unknown period_type: {period_type}")


def _detect_period_type(period_input):
    """Auto-detect period type from input format."""
    if re.match(r'^\d{4}-P\d{2}$', period_input):
        return 'fiscal'
    elif '_' in period_input:
        return 'date-range'
    elif re.match(r'^\d{4}-\d{2}$', period_input):
        return 'calendar-monthly'
    else:
        raise ValueError(
            f"Cannot auto-detect period type from '{period_input}'. "
            "Expected YYYY-MM (calendar-monthly), YYYY-MM-DD_YYYY-MM-DD (date-range), "
            "or YYYY-PNN (fiscal)."
        )


def _resolve_calendar_monthly(period_input):
    """Resolve YYYY-MM to first/last day of month."""
    try:
        dt = datetime.strptime(period_input, '%Y-%m')
    except ValueError:
        raise ValueError(f"Invalid calendar-monthly format: '{period_input}'. Expected YYYY-MM.")

    year = dt.year
    month = dt.month
    _, last_day = calendar.monthrange(year, month)

    return {
        'periodLabel': period_input,
        'periodStart': f"{year:04d}-{month:02d}-01",
        'periodEnd': f"{year:04d}-{month:02d}-{last_day:02d}",
        'periodType': 'calendar-monthly',
    }


def _resolve_date_range(period_input):
    """Resolve YYYY-MM-DD_YYYY-MM-DD to explicit start/end."""
    parts = period_input.split('_')
    if len(parts) != 2:
        raise ValueError(
            f"Invalid date-range format: '{period_input}'. Expected YYYY-MM-DD_YYYY-MM-DD."
        )

    start_str, end_str = parts

    try:
        start_dt = datetime.strptime(start_str, '%Y-%m-%d')
    except ValueError:
        raise ValueError(f"Invalid start date: '{start_str}'. Expected YYYY-MM-DD.")

    try:
        end_dt = datetime.strptime(end_str, '%Y-%m-%d')
    except ValueError:
        raise ValueError(f"Invalid end date: '{end_str}'. Expected YYYY-MM-DD.")

    if start_dt > end_dt:
        raise ValueError(
            f"Start date ({start_str}) must be before or equal to end date ({end_str})."
        )

    return {
        'periodLabel': f"{start_str} to {end_str}",
        'periodStart': start_str,
        'periodEnd': end_str,
        'periodType': 'date-range',
    }


def _resolve_fiscal(period_input, fiscal_calendar_path):
    """Resolve YYYY-PNN via fiscal calendar YAML lookup."""
    if not fiscal_calendar_path:
        raise ValueError(
            "Fiscal calendar path not configured. "
            "Set fiscal_calendar in config.yaml to use fiscal period type."
        )

    if not re.match(r'^\d{4}-P\d{2}$', period_input):
        raise ValueError(f"Invalid fiscal period format: '{period_input}'. Expected YYYY-PNN.")

    try:
        with open(fiscal_calendar_path, 'r') as f:
            cal = yaml.safe_load(f)
    except FileNotFoundError:
        raise ValueError(f"Fiscal calendar file not found: {fiscal_calendar_path}")

    periods = cal.get('periods', [])
    for period in periods:
        if period.get('id') == period_input:
            # F2: Validate required keys exist
            if 'start_date' not in period or 'end_date' not in period:
                raise ValueError(
                    f"Period '{period_input}' in {fiscal_calendar_path} is missing "
                    f"required fields: need both 'start_date' and 'end_date'."
                )
            # F1: Coerce to str — yaml.safe_load parses unquoted dates as datetime.date
            return {
                'periodLabel': period_input,
                'periodStart': str(period['start_date']),
                'periodEnd': str(period['end_date']),
                'periodType': 'fiscal',
            }

    raise ValueError(
        f"Period '{period_input}' not found in fiscal calendar at {fiscal_calendar_path}."
    )

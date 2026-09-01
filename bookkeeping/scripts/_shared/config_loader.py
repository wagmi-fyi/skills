#!/usr/bin/env python3
"""
Shared Config Loader for bookkeeping scripts.
Reads config.yaml from the project's _local-bookkeeping/ directory,
resolves path placeholders, exposes get_db_path().
"""

import os
import sys

import yaml

# Applied when config declares no system of record.
DEFAULT_SOR = 'qbo'

# The default-SoR note is emitted once per process, not once per call.
_SOR_DEFAULT_NOTED = False


def _find_config_path():
    """Locate config.yaml via the BOOKKEEPING_CONFIG_PATH environment variable.

    Scripts run from the global skill directory (~/.claude/skills/bookkeeping/)
    and cannot navigate up to find the project root. The environment variable
    must point to {project-root}/_local-bookkeeping/config.yaml.

    Returns:
        str: Absolute path to config.yaml.

    Raises:
        FileNotFoundError: If BOOKKEEPING_CONFIG_PATH is not set.
    """
    config_path = os.environ.get('BOOKKEEPING_CONFIG_PATH')
    if not config_path:
        raise FileNotFoundError(
            "BOOKKEEPING_CONFIG_PATH not set. "
            "Set it to your project's _local-bookkeeping/config.yaml"
        )
    return os.path.abspath(config_path)


def _resolve_project_root(config_path):
    """Resolve {project-root} from config.yaml location.

    config.yaml is at {project-root}/_local-bookkeeping/config.yaml
    project root = parent of config's parent directory.
    """
    local_dir = os.path.dirname(config_path)
    project_root = os.path.dirname(local_dir)
    return project_root


def load_config():
    """Load module config with all placeholders resolved.

    Resolution order (each pass may depend on previous passes):
        Pass 1: {project-root}
        Pass 2: {local_dir}     (depends on pass 1)
        Pass 3: {module_root}   (resolve ~ to actual home dir)
        Pass 4: {output_folder} (depends on pass 1)

    Returns:
        dict: Config with all path placeholders resolved.

    Raises:
        FileNotFoundError: If BOOKKEEPING_CONFIG_PATH not set or config.yaml not found.
    """
    config_path = _find_config_path()

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config not found at: {config_path}")

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    project_root = _resolve_project_root(config_path)

    # Pass 1: resolve {project-root}
    for key, value in config.items():
        if isinstance(value, str) and '{project-root}' in value:
            config[key] = value.replace('{project-root}', project_root)

    # Pass 2: resolve {local_dir} (depends on pass 1)
    local_dir = config.get('local_dir', '')
    for key, value in config.items():
        if isinstance(value, str) and '{local_dir}' in value:
            config[key] = value.replace('{local_dir}', local_dir)

    # Pass 3: resolve {module_root} (expand ~ to actual home dir)
    module_root = config.get('module_root', '')
    if module_root:
        module_root = os.path.expanduser(module_root)
        config['module_root'] = module_root
    for key, value in config.items():
        if isinstance(value, str) and '{module_root}' in value:
            config[key] = value.replace('{module_root}', module_root)

    # Pass 4: resolve {output_folder} (depends on pass 1)
    output_folder = config.get('output_folder', '')
    for key, value in config.items():
        if isinstance(value, str) and '{output_folder}' in value:
            config[key] = value.replace('{output_folder}', output_folder)

    return config


def get_db_path():
    """Get the full database path from config.

    Returns:
        str: Resolved path to the database file (e.g., "/path/to/database/bookkeeping.db")

    Raises:
        FileNotFoundError: If config.yaml not found.
    """
    config = load_config()
    return os.path.join(config['database_dir'], config['database_name'])


def get_coding_config():
    """Get coding workflow confidence thresholds from config.

    Returns:
        dict: {min_confidence_to_categorize: int, min_confidence_to_auto_approve: int}

    Raises:
        FileNotFoundError: If config.yaml not found.
    """
    config = load_config()
    coding = config.get('coding', {})
    return {
        'min_confidence_to_categorize': coding.get('min_confidence_to_categorize', 5),
        'min_confidence_to_auto_approve': coding.get('min_confidence_to_auto_approve', 9),
    }


def get_sor():
    """Get the declared system of record.

    The SoR is declared in config, never detected from the environment. The
    binding is `default_system_of_record`; it also drives publish-adapter
    resolution as the {sor} token (see operations/process-period.md).

    An absent or empty declaration defaults to QBO and emits a note to stderr —
    once per process, and to stderr so the JSON stdout contract stays clean.
    A declared value is honored verbatim and passes silently.

    Returns:
        dict: {sor: str, declared: bool, raw: str}
              sor    — lowercase resolution token (e.g. "qbo", "mock")
              declared — False when the default was applied
              raw    — the value as written in config, "" when absent

    Raises:
        FileNotFoundError: If config.yaml not found.
    """
    global _SOR_DEFAULT_NOTED

    config = load_config()
    raw = (config.get('default_system_of_record') or '').strip()

    if raw:
        return {'sor': raw.lower(), 'declared': True, 'raw': raw}

    if not _SOR_DEFAULT_NOTED:
        print(
            "NOTE: no system of record declared in config "
            "(default_system_of_record is empty) — assuming QBO. "
            "Declare it to silence this note.",
            file=sys.stderr,
        )
        _SOR_DEFAULT_NOTED = True

    return {'sor': DEFAULT_SOR, 'declared': False, 'raw': ''}


def get_period_config():
    """Get period configuration from config.

    Returns:
        dict: {period_type: str, fiscal_calendar: str}
              period_type defaults to 'calendar-monthly', fiscal_calendar defaults to ''.
              {local_dir} placeholders are resolved in fiscal_calendar path.

    Raises:
        FileNotFoundError: If config.yaml not found.
    """
    config = load_config()
    period_type = config.get('period_type', 'calendar-monthly')
    fiscal_calendar = config.get('fiscal_calendar', '')

    # Resolve {local_dir} placeholder in fiscal_calendar path
    if fiscal_calendar and '{local_dir}' in fiscal_calendar:
        local_dir = config.get('local_dir', '')
        fiscal_calendar = fiscal_calendar.replace('{local_dir}', local_dir)

    return {
        'period_type': period_type,
        'fiscal_calendar': fiscal_calendar,
    }

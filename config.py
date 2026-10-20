"""
config.py: reads secret API keys from the .env file, so they never sit in the code.

The .env file lives next to these scripts and looks like:
    PROPLINE_KEY=your_key_here
    ODDS_API_KEY=your_key_here
It is listed in .gitignore, so it is never uploaded to GitHub.
A real environment variable with the same name takes priority over the file.
"""

import os

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _read_env_file():
    values = {}
    if not os.path.exists(ENV_FILE):
        return values
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def get_secret(name):
    """The secret's value, or an empty string if it isn't set anywhere."""
    return os.environ.get(name) or _read_env_file().get(name, "")

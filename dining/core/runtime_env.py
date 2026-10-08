"""Load only an explicitly selected server-side configuration file."""

from pathlib import Path


def load_env_file(path: Path | str | None) -> None:
    """Load KEY=value pairs from a .env file without overriding existing variables."""
    if path is None:
        return
    from dotenv import load_dotenv

    try:
        with Path(path).open(encoding="utf-8") as stream:
            load_dotenv(stream=stream, override=False, interpolate=False)
    except (OSError, UnicodeError):
        raise ValueError("The selected environment file could not be read") from None

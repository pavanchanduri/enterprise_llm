"""
Configuration loader — reads config.yaml and .env into a single Config object.
"""

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Find project root (where config/ directory is)
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def load_config():
    """Load configuration from YAML file and environment variables."""
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)

    # Override with environment variables if set
    config["api_key"] = os.getenv("ANTHROPIC_API_KEY", "")

    # Resolve relative paths to absolute
    for key, path in config.get("paths", {}).items():
        if isinstance(path, str) and path.startswith("./"):
            config["paths"][key] = str(PROJECT_ROOT / path[2:])

    return config


# Global config instance
CONFIG = load_config()

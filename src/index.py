#!/usr/bin/env python3
"""
twilio-workflow-engine - twilio workflow engine implementation
Built by Retsumdk
"""
import functools, json, logging, os, sys, time
from pathlib import Path
from typing import Any
try:
    import click
    import yaml
    from dotenv import load_dotenv
    import requests
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install requests pyyaml python-dotenv click")
    sys.exit(1)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)


def retry(times: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """Retry a callable with exponential backoff before re-raising."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt, wait = 1, delay
            while True:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    if attempt >= times:
                        raise
                    logger.warning("attempt %d/%d failed: %s (retry in %.1fs)", attempt, times, exc, wait)
                    time.sleep(wait)
                    attempt += 1
                    wait *= backoff

        return wrapper

    return decorator


class Config:
    DEFAULTS = {"base_url": "https://api.example.com", "timeout": 30, "retries": 3}
    def __init__(self, path=None):
        self._d = self.DEFAULTS.copy()
        if path and Path(path).exists():
            with open(path) as f:
                self._d.update(yaml.safe_load(f) or {} if path.endswith((".yaml","yml")) else json.load(f))
        for k in self.DEFAULTS: self._d[k] = os.environ.get(k.upper(), self._d[k])
    def __getitem__(self, k): return self._d[k]

class Client:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "twilio-workflow-engine/1.0"
    @retry(times=3)
    def run(self) -> dict:
        logger.info(f"Connecting to: {self.cfg['base_url']}")
        result = {"status": "success", "config": dict(self.cfg._d)}
        logger.info("Execution completed")
        return result
    def close(self): self.session.close()

@click.group()
@click.version_option(version="1.0.0")
def cli() -> None:
    """twilio workflow engine implementation"""

@cli.command()
@click.option("--config", "-c", type=str)
@click.option("--verbose", "-v", is_flag=True)
def run(config, verbose):
    if verbose: logging.getLogger().setLevel(logging.DEBUG)
    try:
        client = Client(Config(config))
        click.echo(json.dumps(client.run(), indent=2))
        client.close()
    except Exception as e:
        logger.error(f"Error: {e}"); sys.exit(1)

if __name__ == "__main__": cli()

# twilio-workflow-engine

twilio workflow engine implementation

## Features

- Production-ready Python 3.11+ code with full type hints
- Retry/backoff, structured logging, and config layering (file + env)
- Test suite wired up with pytest
- Built by Retsumdk

## Installation

```bash
git clone https://github.com/Retsumdk/twilio-workflow-engine.git
cd twilio-workflow-engine
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
```

## Usage

```bash
python3 src/index.py --help
```

## Development

```bash
pytest -q
```

## Configuration

Create `config.yaml` (or `config.json`) for custom settings, or set environment variables (`BASE_URL`, `TIMEOUT`, `RETRIES`) that override the file.

## License

MIT License

---

Built by [Retsumdk](https://github.com/Retsumdk)

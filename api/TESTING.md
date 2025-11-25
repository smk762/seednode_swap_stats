## Testing the API logic

This project uses `pytest` for unit tests that validate internal logic (event parsing, store operations, and basic aggregation). You can run tests locally or via a separate Docker container so the API keeps running.

### Prerequisites
- Python 3.11+ (for local runs)
- `docker` and `docker compose` (for containerized runs)

### Run tests locally
From the `api/` directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

### Run tests in a separate container
This repo defines a dedicated `api_tests` service. It mounts the API source and tests into a container based on the same image used by the API service.

Keep the app running (optional):

```bash
docker compose up -d api
```

Run the tests in a separate, ephemeral container:

```bash
docker compose run --rm api_tests
```

You can also bring up only the test container (it will run once and exit):

```bash
docker compose up --build api_tests
```

### Test locations
- Tests live under `api/tests/`.
  - `test_events.py`: covers grouped event parsing and pair matching.
  - `test_store.py`: covers symbol normalization, upsert/indexing, pruning, and basic aggregation.

### Notes
- Tests avoid network access and external services.
- The compose test service reuses `api/Dockerfile` and `api/requirements.txt`, so adding new test dependencies should be done in `api/requirements.txt`.













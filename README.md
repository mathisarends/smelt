# smelt

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # create .venv and install dev dependencies
uv run pre-commit install
```

Common commands:

```bash
uv run pytest                        # run tests
uv run pytest --cov                  # run tests with coverage
uv run ruff check --fix .            # lint
uv run ruff format .                 # format
uv run mypy                          # type-check
uv run pre-commit run --all-files    # run all hooks
```

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).

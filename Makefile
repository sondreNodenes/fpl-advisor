.PHONY: install test lint format run

install:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

run:
	uv run uvicorn fpl_advisor.app:app --reload

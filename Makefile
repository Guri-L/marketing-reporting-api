.PHONY: install seed run test lint typecheck check docker-build docker-run clean

install:
	pip install -e ".[dev,bigquery]"

seed:            ## build the local DuckDB database from data/seed
	python -m marketing_api.seed

run: seed        ## local server with auto-reload, auth off
	AUTH_DISABLED=true LOG_FORMAT=text uvicorn marketing_api.main:app_factory --factory --reload

test:
	pytest -q

lint:
	ruff check .

typecheck:
	mypy

check: lint typecheck test

docker-build:
	docker build -t marketing-reporting-api:local .

docker-run:      ## http://localhost:8000/docs, key: local-dev-key
	docker compose up --build

clean:
	rm -rf data/*.duckdb data/*.duckdb.wal .pytest_cache .ruff_cache .mypy_cache build dist

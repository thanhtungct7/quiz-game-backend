.PHONY: conda-update install run test lint format migrate migration docker-up docker-down

CONDA_ENV := backend
CONDA_RUN := conda run -n $(CONDA_ENV)

conda-update:
	conda env update -n $(CONDA_ENV) -f environment.yml

install: conda-update

run:
	$(CONDA_RUN) uvicorn app.main:app --reload

test:
	$(CONDA_RUN) pytest

lint:
	$(CONDA_RUN) ruff check .
	$(CONDA_RUN) mypy app

format:
	$(CONDA_RUN) ruff format .
	$(CONDA_RUN) ruff check --fix .

migrate:
	$(CONDA_RUN) alembic upgrade head

migration:
	$(CONDA_RUN) alembic revision --autogenerate -m "$(m)"

docker-up:
	docker compose up --build

docker-down:
	docker compose down

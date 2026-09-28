.PHONY: install test lint serve web build-web
install: ; pip install -e ".[dev]" && cd frontend && npm install
test: ; pytest -q
lint: ; lint-imports && ruff check core backend tests
serve: ; python -m backend.cli serve
web: ; cd frontend && npm run dev
build-web: ; cd frontend && npm run build

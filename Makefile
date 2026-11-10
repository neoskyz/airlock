.PHONY: dev build test check

dev:
	bash scripts/dev.sh

build:
	bash scripts/frontend-build.sh

test:
	uv run pytest

check: test build

.PHONY: dev api web typegen test test-api install-web

# Boot the FastAPI backend on :8765.
api:
	uvicorn api.main:app --reload --host 127.0.0.1 --port 8765

# Boot the Next.js frontend on :3000. Requires `make install-web` once.
web:
	cd web && npm run dev

# Run both concurrently — see scripts/dev.sh.
dev:
	./scripts/dev.sh

# Install npm deps for the frontend.
install-web:
	cd web && npm install

# Regenerate web/lib/types.ts from the running backend's OpenAPI spec.
# Requires the backend to be reachable on :8765 (run `make api` first).
typegen:
	cd web && npm run typegen

# Backend tests only.
test-api:
	pytest tests/api/ -q

# Full test suite.
test:
	pytest -q

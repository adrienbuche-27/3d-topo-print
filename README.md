# 3D Topo Print

Design 3D-printable terrain models with a GPX route as a separate colour insert.

Work in progress — see [docs/PLAN.md](docs/PLAN.md) for the architecture and progress. A full README comes with v1.

## Development

Requirements: Python 3.11+ with [uv](https://docs.astral.sh/uv/), Node.js 20+.

```bash
./scripts/dev.sh
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000 (docs at `/docs`)

Checks:

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd frontend && npm run lint && npm run format:check && npm run build
```

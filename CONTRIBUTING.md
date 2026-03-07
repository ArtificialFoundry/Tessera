# Contributing to Tessera

Thank you for your interest in contributing to Tessera! This document explains
how to get involved.

## Code of Conduct

By participating in this project, you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md). Be respectful, constructive, and
welcoming.

## Ways to Contribute

- **Bug reports** — file an issue with steps to reproduce
- **Feature requests** — open an issue describing the use case
- **Code** — fix bugs, add features, improve docs
- **Documentation** — fix typos, clarify guides, add examples
- **Testing** — add test coverage, report edge cases

## Getting Started

### Prerequisites

- Python ≥ 3.12
- Node.js ≥ 22
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
git clone https://github.com/ArtificialFoundry/tessera.git
cd tessera

# Python backend
uv sync --all-extras

# Frontend
cd frontend && npm install
```

### Running quality checks

All checks must pass before submitting a PR:

```bash
uv run pytest                          # Tests
uv run ruff check src/ tests/          # Linting
uv run ruff format --check src/ tests/ # Format check
uv run mypy src/                       # Type checking (strict)
```

### Frontend build

```bash
cd frontend && npx vite build
```

### Running locally

```bash
uv run uvicorn tessera.app:create_app --factory --reload --port 8780
```

## Submitting Changes

### Workflow

1. Fork the repository
2. Create a feature branch from `main` (`git checkout -b feature/my-change`)
3. Make your changes
4. Run all quality checks (see above)
5. Commit with a descriptive message
6. Push to your fork
7. Open a Pull Request against `main`

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) style:

```
feat: add DHCP scope import endpoint
fix: correct voter PSK validation on empty string
docs: update deployment guide for multi-server setup
test: add coverage for CIDR bind_ip matching
refactor: extract health check logic from failover engine
```

### Signing

We require a [Developer Certificate of Origin](https://developercertificate.org/)
sign-off on all commits. Add `-s` to your commit command:

```bash
git commit -s -m "feat: my change"
```

This adds a `Signed-off-by: Your Name <email>` line, certifying you have the
right to submit the code under the project's license.

### Pull Request guidelines

- **One logical change per PR** — don't bundle unrelated fixes
- **Include tests** for new functionality
- **Update docs** if behavior changes
- **Keep PRs focused** — smaller PRs get reviewed faster
- All quality checks must pass in CI

## Architecture Overview

See [docs/architecture.md](docs/architecture.md) for a detailed system design.

Key conventions:

- **Engines** subclass `Engine`, declare `depends_on`, register in `deps.py`
- **API routes** go under `src/tessera/api/v1/`, schemas in `schemas.py`
- **Frontend pages** are standalone Preact islands in `frontend/src/pages/`
- **Signals** (`@preact/signals`) for global state, not component state
- **Strict typing** — mypy must pass with zero errors
- **Read vs write auth** — read endpoints unauthenticated, write endpoints use `require_admin`

## Reporting Security Issues

**Do not open a public issue for security vulnerabilities.**

Email security reports to the maintainers privately. See [SECURITY.md](SECURITY.md)
for details.

## License

By contributing, you agree that your contributions will be licensed under the
[GNU General Public License v3.0](LICENSE).

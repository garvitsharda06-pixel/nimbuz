# Dalal Desk tasks. Backend venv lives in backend/.venv
DALAL := backend/.venv/bin/dalal

.PHONY: setup test lint synth synth-edge check-data verify download-data backtest replay live serve ui build-ui e2e

setup:
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
	cd frontend && npm ci

test:
	cd backend && .venv/bin/python -m pytest -q
	cd backend && .venv/bin/ruff check dalal tests
	cd backend && .venv/bin/mypy dalal

lint:
	cd backend && .venv/bin/ruff check --fix dalal tests

synth:            ## SYNTHETIC dataset for plumbing/demo (never market data)
	$(DALAL) synth

synth-edge:       ## SYNTHETIC dataset with a planted momentum edge (known-answer demo)
	$(DALAL) synth --momentum 0.6 --days 500

check-data:
	$(DALAL) check-data

verify:           ## probe Upstox with your token; writes data/verify_report.json
	$(DALAL) verify

download-data:    ## >=3y of 1-minute candles from Upstox (rate-limited, incremental)
	$(DALAL) download-data

backtest:         ## walk-forward research -> statsbook + tearsheets
	$(DALAL) backtest

replay:           ## API + UI + replay of the latest stored day at 30x
	$(DALAL) serve --replay --speed 30

live:             ## live mode (needs UPSTOX_ANALYTICS_TOKEN and data.provider: upstox)
	$(DALAL) serve --live

serve:
	$(DALAL) serve

ui:
	cd frontend && npm run dev

build-ui:
	cd frontend && npm run build

e2e:
	cd frontend && npx playwright test

.PHONY: up down migrate test lint eval eval-live collect run serve demo
up:        ; docker compose up -d --wait
down:      ; docker compose down
migrate:   ; uv run signal migrate
test:      ; uv run pytest -q
lint:      ; uv run ruff check src tests evals scripts && uv run mypy src
eval:      ; uv run python evals/run_evals.py --stage rules
eval-live: ; uv run python evals/run_evals.py --stage qualify
collect:   ; uv run signal collect --source hn
run:       ; uv run signal run
serve:     ; uv run signal serve
demo:      ; uv run python scripts/seed_demo.py && SIGNAL_DATABASE_URL=postgresql://signal:signal-local-only@127.0.0.1:55433/signal_demo uv run signal serve

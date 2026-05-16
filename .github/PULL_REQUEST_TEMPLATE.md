## What

<!-- One sentence: what does this change? -->

## Why

<!-- One sentence: bug? spec? user report? Link the issue if there is one. -->

## How

<!-- Architectural notes only — don't restate the diff. If this introduces a
     non-obvious choice (new dependency, schema change, threading model),
     either explain in 2-3 lines here or add an ADR under docs/adr/. -->

## Type of change

- [ ] Bug fix (behaviour now matches docs)
- [ ] Feature (new capability) — version target: v0.__
- [ ] Refactor / cleanup (no behaviour change)
- [ ] Documentation
- [ ] Cloud / infrastructure

## Verification

<!-- How you proved this works. Be specific: test names, commands, screenshots. -->

- [ ] Unit tests added or updated, and they pass locally
- [ ] Manual verification against `docker compose up -d`
- [ ] Loom or screenshot attached if the UI changed

## Checklist

- [ ] Touched only files relevant to the stated scope (no opportunistic refactors snuck in)
- [ ] No new external dependency without a one-line "why this one, not the alternatives" note
- [ ] Public API change → updated README / SDK README / migration note
- [ ] No API keys, `.env` contents, or other secrets in the diff

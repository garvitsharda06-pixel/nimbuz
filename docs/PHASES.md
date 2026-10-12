# Phase reports

## Phase 0: Plan (done)

**Built**
- `docs/BRIEF.md`: the brief.
- `docs/DECISIONS.md`: default answers to §14 (D-001), the definitions of probability, ranking
  and gate, and the verified facts with sources and check dates (Upstox endpoints from the
  official SDK source; charges, STT, SEBI algo rules and holidays via web search).
- `docs/ARCHITECTURE.md`, `docs/PLAN.md`, `CLAUDE.md`.

**Hard truths up front**
- The build sandbox has **no access to broker or exchange hosts**. Everything is built and
  tested against a clearly labelled **synthetic** dataset. No real-market edge can be claimed
  from this repo until `make download-data && make backtest` runs on the owner's machine with
  an Upstox token.
- The "official Upstox agent skill packs" mentioned in the brief could not be found (searched
  2026-10-12). None were installed.
- Facts we could only check through search are tagged `VERIFY-ON-FIRST-RUN`.
  `make verify` re-checks the API ones automatically.

**Next**: Phase 1, data layer and replay.

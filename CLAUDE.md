# Claude Code Project Settings

## Quick Start

**Global commands (work everywhere):**

```
/onboard              # Read latest handoff + get oriented
/handoff              # Create session handoff for next time
```

Or manually:
```bash
cat worklogs/handoffs/session-latest.md
```

## Scratch & Generated Files — Keep the Repo Root Clean

NEVER write scratch, test, or generated artifacts to the repo root. They pollute
the package. Put them under `worklogs/` (gitignored) instead:

- Throwaway plan YAMLs, sample plans, experiment configs → `worklogs/scratch/`
- Test-run output / captured logs → `worklogs/` (or pipe to `/tmp`)
- Session handoffs → `worklogs/handoffs/`
- Working plan docs → `docs/plans/*.md` (gitignored; only `docs/plans/completed/` is tracked)
- Agent/tool working dirs are local-only and globally ignored

The root must stay limited to real package files (`src/`, `tests/`, `docs/`,
packaging, README). If a tool needs a path, point it at `worklogs/`.

## Environment

- **Python:** 3.9+
- **Package:** minimise (pip install -e .)
- **Entry Point:** `mini` command
- **Tests:** `pytest tests/ -v` (42/42 passing)
- **Config:** `~/.minimise/` (auto-created)

## Session Handoff

At end of each session:
```bash
cp worklogs/handoffs/HANDOFF_TEMPLATE.md worklogs/handoffs/session-YYYY-MM-DD-HHmm.md
# Fill in: what was done, what's next, how to run, gotchas, current state
ln -sf session-YYYY-MM-DD-HHmm.md worklogs/handoffs/session-latest.md
```

Next session, read the handoff to get context.

## Key Commands

```bash
# Orient yourself
cat worklogs/handoffs/session-latest.md

# Run tests
pytest tests/ -v

# Try the tool
mini job new --plan examples/example-plan.yaml
mini job list

# View docs
cat README.md
cat TESTING.md
```

## Implementation Preference: Dogfooding via `mini`

When implementing features or fixes:
1. **Prefer `mini` commands** as the primary method — this dogfoods the tool and validates real-world usage
2. **Only fall back** to direct code changes when:
   - The feature is blocked in `mini` (e.g., WebSocket handlers not yet implemented)
   - The fix requires direct code changes that `mini` cannot invoke (e.g., schema migrations, internal refactors)
   - Direct changes make the dogfooding path clearer (rare)

This keeps the tool dogfood-friendly and ensures CLI/API actually work.

# Claude Code Refactoring & Quality Standards

## Anti-Slop Guidelines
- Never generate speculative code, placeholder interfaces, or "future-proofing" abstractions.
- Adhere strictly to YAGNI: Use native language features and existing project utils before writing new helpers. Prefer the simplest thing that works — favor one-liner solutions over multi-line scaffolding where it stays readable.
- Prioritize low cognitive complexity; split nested conditional logic into early-returns.
- Do not add external dependencies unless explicitly requested.
- Implement strict Gall's Law: build the simplest working system, completely ignoring future scale or features.

## Refactoring Workflow
1. Run local tests before making changes.
2. Perform surgical edits rather than rewriting intact architectural blocks.
3. Eliminate any dead code, unused exports, or duplicate logic blocks introduced during edits.

# Claude Code Architecture & Extensibility Standards

## Architectural Blueprint
- Enforce clean architecture boundaries: Keep core domain logic entirely decoupled from external frameworks, databases, and network clients.
- Depend on abstractions (interfaces/abstract classes), never on concrete implementations.

## Design Patterns for Extensibility
- **Open-Closed Principle:** When adding new behaviors or variations, use polymorphism or strategy patterns rather than adding branches to existing `switch` or `if/else` statements.
- **Dependency Injection:** Explicitly inject all dependencies via constructors. No global state, singletons, or inline instantiations of sub-services.
- Ensure all modules have a single responsibility. If a class or file exceeds 250 lines, evaluate it for split-off extraction.

## Pre-Flight Design Protocol
Before making any architectural changes or generating a new feature sub-system:
1. Present a brief, text-only design layout explaining the interface boundaries you intend to create.
2. Outline exactly how a developer would extend this feature in the future without modifying your newly written modules.
3. Wait for human confirmation before generating the actual code files.

## Dependency vs. Custom Code Protocol
Before writing any non-trivial business logic, utility function, or layout element:
1. **Research Phase:** Check if the problem can be natively solved or handled by an existing dependency in the project's package manifest.
2. **Trade-off Analysis:** If a new library is required, provide a 3-bullet pros/cons list contrasting:
   - *Option A:* Pulling in a highly stable, popular external dependency (evaluating bundle size / security).
   - *Option B:* Building a minimalist, extensible version from scratch (evaluating long-term maintenance overhead and testing requirements).
3. **Decision Gate:** Present this analysis to the user and await explicit confirmation on whether to "Build" or "Import" before generating code.

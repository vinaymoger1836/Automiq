# AGENTS.md — Agentic Workflow Automation Engine

> Repository-wide instructions for OpenAI Codex and other coding agents. This is the Codex equivalent of a root `CLAUDE.md`. Keep this file at the repository root. Read it before planning, changing, or executing code.

## 1. Source of truth and instruction precedence

1. Follow the user's current explicit instructions and any higher-priority system/developer instructions.
2. Follow this `AGENTS.md` for **how** engineering work is carried out.
3. Follow `architecture.md` for **what** to build, system boundaries, technology choices, security requirements, and data contracts.
4. Follow `phase-planning.md` for **when** to build it, scoped deliverables, task IDs, and phase gates.
5. Follow existing code conventions when they do not conflict with the above.

**Important overrides:** The original `phase-planning.md` may call for unit/component tests and automated Git commits. Those instructions are superseded by the E2E-first and no-commit/no-push rules in this file. Do not silently change architecture; document necessary deviations and ask for user approval on major changes. If documents disagree on system design, follow `architecture.md` and flag the discrepancy.

## 2. Session startup protocol

At the start of every Codex session:

1. Read `AGENTS.md`, `architecture.md`, and the active portion of `phase-planning.md`.
2. Run `git status --short` to identify user changes; never discard or overwrite unrelated changes.
3. Inspect relevant files and available tooling rather than assuming paths, commands, or interfaces exist.
4. Identify the current phase and the smallest coherent end-to-end slice to implement.
5. Briefly state the proposed files to touch, expected behavior, risk areas, and E2E verification approach before a substantial change.
6. Work only within the requested phase or feature. Do not start the next phase without user authorization, even when a gate passes.

## 3. Architecture requirements — non-negotiable

- Build the system described in `architecture.md`: Next.js + TypeScript UI, FastAPI control plane, Temporal durable orchestration, LangGraph bounded AI nodes, PostgreSQL persisted business state, Redis for supported auxiliary uses, and approved external connectors.
- Separate control plane from execution plane. Keep Temporal workflow logic deterministic; network access, LLM calls, random data, database calls, and other side effects belong in activities.
- Pin each execution to an immutable published workflow version. Store traceable execution and step statuses.
- Assume retryable external activities may run more than once. Use idempotency keys, deduplication, or explicit reconciliation for side effects. Never claim guaranteed exactly-once external effects.
- Enforce workspace-scoped authorization server-side. Never trust the UI as a security boundary.
- Restrict agent tool permissions, maximum iterations, time/token/cost budgets, and human approval for sensitive actions.
- Do not add new external infrastructure, major frameworks, or architectural patterns without first explaining the tradeoff and obtaining approval.
- Favor cohesive vertical slices (UI + API + persistence + workflow behavior + observable result) over disconnected scaffolding.

## 4. UI/UX and responsive design

**The UI must be visually appealing, polished, usable, and accessible**—not a default admin template or raw component library demo.

- Support **both dark and light themes** from the beginning, using semantic design tokens (CSS variables or equivalent). Theme selection should respect `prefers-color-scheme` initially, allow a visible manual switch, persist the user's preference, and avoid hydration/theme flicker.
- Ensure every new screen/component works in both themes: legible contrast, distinct surfaces, readable charts, valid focus rings, visible hover/disabled/loading/error states. Do not hardcode text/background colors that work in only one theme.
- Responsive layouts are required for mobile (~375px), tablet (~768px), laptop (~1280px), and wide desktop (~1440px+). No horizontal page overflow. The node editor may offer a mobile-optimized read-only or simplified editing experience, but workflows, status, and navigation must remain usable on small screens.
- Build a coherent visual system: intentional typography, spacing scale, alignment, semantic status colors, restrained animations, polished empty/error/loading states, and consistent icons.
- Meet practical accessibility expectations: keyboard navigation, labeled controls, screen-reader-friendly status text, visible focus, appropriate contrast, reduced-motion support, and no critical action relying on color alone.
- For workflow editor features, keep nodes, edges, labels, execution state, pan/zoom, and inspector usable. Account for dense graphs and smaller screens.
- Do not add heavy decorative effects at the expense of interaction speed, clarity, or accessibility.
- When UI is changed, capture screenshots in **both themes** and at **desktop + mobile** viewport widths during E2E verification.

## 5. Security, secrets, privacy, and Git hygiene

**Never commit, push, paste, publish, upload, or otherwise expose secrets or private data to any remote Git repository, public space, issue, log sharing service, or AI prompt.**

- Treat credentials, API keys, OAuth tokens, session cookies, signing keys, private certificates, production URLs containing credentials, database dumps, customer data, and personal information as sensitive.
- Use environment variables or a dedicated secret manager. Only commit sanitized `.env.example` files containing placeholder values. Never include real keys in source code, fixtures, snapshots, screenshots, demos, docs, or commands.
- Use a `.gitignore` for `.env`, `.env.*` (except `.env.example`), `.secrets/`, local databases, credential files, E2E evidence, traces, screenshots, browser profiles, and test reports. Make sure evidence paths really are ignored before running tests.
- Do not display raw secrets in terminal output. Sanitize logs, stack traces, HTTP recordings, and saved artifacts; use synthetic datasets and local/mock providers for E2E by default.
- Before finalizing changes, review `git diff` and `git status`; run an available secret scan of tracked/changed files, or perform a careful manual scan if no scanner exists. A clean scan does not authorize publishing data.
- If any suspected secret is found in tracked history, stop, report the path and risk **without repeating its value**, and recommend rotating it; do not rewrite Git history or contact remote services without user approval.
- Maintain backend protection against SSRF, prompt injection, tenant data leakage, untrusted tool output, insecure webhook handling, and unsafe agent actions as described in the architecture.
- **Git is read-only unless the user explicitly authorizes a specific write action.** Allowed: `git status`, `git diff`, `git log`, `git show`. Do **not** run `git add`, `git commit`, `git push`, `git tag`, `git reset --hard`, `git clean`, `git rebase`, branch creation/deletion, or other Git state-mutating commands by default. The user alone handles commits and pushes. Do not configure automatic commits or release workflows that publish code.

## 6. Testing policy — strongly E2E-first

**Do NOT write unit tests after writing implementation code. Strongly prefer end-to-end (E2E) tests as the sole behavioral testing mechanism.** Do not add a unit test for every function, class, endpoint, or code change. Do not create routine component tests, unit snapshots, or isolated test suites just to increase coverage.

- Validate complete user-visible flows across actual UI/API/database/Temporal worker boundaries when reasonable. Prefer Playwright for browser E2E and a reproducible Docker Compose local/test environment with synthetic data and deterministic fake external providers.
- For complex features, implement or update an E2E test exercising the **completed workflow**, including important failure and recovery paths. Examples: webhook → workflow run → AI triage with stubbed LLM → approval → GitHub/Slack fake connector → audit trail; and worker restart → durable resumption → correct final state.
- Use deterministic provider stubs for GitHub, Slack, and LLM calls; do not make paid or real external API calls during E2E tests unless the user explicitly requests them.
- Non-behavioral checks such as formatting, linting, static type checking, builds, schema validation, dependency scans, and secret scans are **allowed and expected**. They are not a substitute for E2E verification.
- **Rare isolation exception:** If a system truly must be tested in isolation because the failure mode cannot reasonably be covered E2E (e.g., a critical deterministic safety algorithm), first **document all plausible failure modes** and the justification for isolation. Then **write the isolated checks before writing the implementation code**, and implement against those checks. Never retroactively add unit tests after implementation as a default practice. Record the exception in the feature notes or an ADR.
- Do not delete pre-existing tests solely for being unit tests; maintain existing required checks when modifying code that relies on them, unless the user asks to migrate the suite.
- Do not claim a test passed unless it actually ran and passed. If E2E cannot run because a dependency is unavailable, state what is blocked and what was and was not verified.

### Required E2E evidence for complex features

Each completed complex feature must end with a **verifiable, repeatable artifact**:

1. **Exact command** executed, including relevant environment setup, service startup, seed data, and env-file template or variable names (never secret values).
2. **Observed result:** pass/fail, number of scenarios, relevant behavior, and reproducible failures, if any.
3. **Saved evidence** under a Git-ignored location, for example `artifacts/e2e/<feature-name>/`, including at least one machine-readable test report and traces or screenshots where applicable. Use Playwright HTML/JUnit report, trace ZIP, or sanitized screenshots; preserve failure evidence.
4. **Environment:** runtime versions, test configuration, local service versions or Compose profile, browser and viewport where relevant, and fake-provider configuration.
5. **Re-run instructions** in the handoff, so a developer can reproduce the result from a fresh checkout with sanitized `.env.example` and local setup.

Default conventions (adapt only if repository tooling differs):

```bash
# Run from repository root after setting up local services and non-secret test config.
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
pnpm --dir apps/web exec playwright test --reporter=html,junit
# Check artifacts/e2e/ for sanitized, ignored reports/traces/screenshots.
```

These are **illustrative commands**, not proof that such files, scripts, or paths already exist. Create/wire them as part of the applicable phase and report actual commands used.

## 7. Implementation conventions

- Prefer clear, maintainable code and typed contracts over clever abstractions. Keep FastAPI request/response schemas explicit and Next.js/TypeScript strict.
- Database changes require migrations and compatible API/data-model handling; never silently destroy existing data.
- Keep error handling, retries, timeouts, and observability explicit. Log correlation IDs and execution IDs, not private payloads.
- Include meaningful UX for loading, empty, failed, offline/reconnecting, and approval-pending states.
- Keep dependencies minimal, pinned/locked as appropriate, and maintained. Do not introduce a dependency for trivial functionality.
- Never bypass access controls, ignore failures, silently swallow errors, or fabricate metrics/test results to satisfy a phase gate.

## 8. Phase gates and handoff format

- Use the IDs and acceptance criteria in `phase-planning.md`. Tick an item only when its functional work and relevant E2E evidence are actually complete.
- When a phase-plan checkbox mentions routine unit/component tests, interpret the verification obligation using **Section 6** instead; replace with equivalent end-to-end verification where feasible. Leave a note explaining substitutions rather than falsely marking the original test type as performed.
- Stop at the requested phase gate; summarize unresolved work and wait for the user's direction before starting the next phase.
- At the end of each run, report: **scope and changed files; architectural decisions; UI dark/light + responsive checks when relevant; exact verification commands; observed pass/fail; ignored evidence paths; security/secret scan results; known issues; next recommended task; Git status (uncommitted, unpushed).**
- Do not say work is production-ready, secure, tested, or deployed unless that claim is supported by actual verification.

## 9. Completion checklist

- [ ] Implemented behavior matches `architecture.md` and the active phase.
- [ ] No unexplained architectural deviation or unrelated file mutation.
- [ ] Light and dark themes reviewed for any UI work.
- [ ] Mobile/tablet/desktop responsive behavior reviewed for any UI work.
- [ ] Security and workspace boundaries considered; secrets not added to tracked files or test evidence.
- [ ] No post-implementation unit tests added; E2E coverage or documented pre-implementation isolation exception used.
- [ ] Complex feature E2E command, observed result, and ignored saved evidence supplied.
- [ ] Relevant lint, types, build, migrations, and security checks executed or clearly marked not run.
- [ ] `git status` and `git diff` reviewed; **no commit or push performed**.
- [ ] Phase checklist and concise handoff updated.

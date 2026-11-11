<!--
Sync Impact Report
==================
Version change: (unversioned template) → 1.0.0
Bump rationale: initial adoption. Every template placeholder is replaced; no prior
  ratified version exists.

Sources:
  - assignment.md (the authority; see Principle I).
  - Engineering rules adapted from the LLM-Stereotypes constitution v1.2.0 (typing with ty,
    ruff, pytest, KISS/YAGNI/DRY, SOLID/Law of Demeter/low coupling/Convention over
    Configuration, pre-commit + GitHub Actions gates, governance). Project-specific parts
    of that document (harness/suites split, statistics, MockBackend, model extras,
    `legacy/`) are not carried over; they have no counterpart here.

Principles (template slot → adopted title):
  PRINCIPLE_1 → I. Assignment Fidelity (NON-NEGOTIABLE)
  PRINCIPLE_2 → II. Typed Code, Checked by ty
  PRINCIPLE_3 → III. One Linter and Formatter: ruff
  PRINCIPLE_4 → IV. Tests Are the Contract: pytest, offline
  PRINCIPLE_5 → V. Simplicity: KISS, YAGNI, DRY
  Added       → VI. Structure: SOLID, Law of Demeter, Low Coupling, Convention over
                Configuration

Added sections:
  - Quality Gates and Automation (SECTION_2)
  - Project Constraints (SECTION_3)
  - Governance

Removed sections: none

Interpretation notes:
  - "No deviation from assignment.md" is read as: nothing may contradict it, omit a
    requirement or deliverable it lists, or substitute a technology it mandates. Choices the
    assignment leaves open (e.g., target website, database engine, face model among those
    DeepFace offers, thresholds) MAY be made, but MUST be recorded as decisions and traced to
    the assignment text they implement.
  - The mission briefs (image-indexer-brief.md, search-script-brief.md) are subordinate to
    assignment.md; where a brief and the assignment disagree, the assignment wins.

Follow-up TODOs (repository changes, not made by this amendment):
  - Add pyproject.toml with [tool.ruff] and [tool.ty], .pre-commit-config.yaml, and
    .github/workflows/ci.yml so that the Quality Gates are enforced.
-->

# photo-crawler Constitution

## Core Principles

### I. Assignment Fidelity (NON-NEGOTIABLE)

- `assignment.md` is the single authoritative source of requirements. Every spec, plan,
  task, and line of code MUST trace to a requirement in it. Nothing MAY contradict it.
- The system MUST implement every item the assignment lists, with no omissions:
  - Pipeline: (1) scrape images from the given website that contain people with visible
    faces; (2) convert all images to a single format and resize them to a fixed maximum
    size; (3) detect faces and extract embeddings for all detected faces with a pre-trained
    deep learning model; (4) store image metadata, face bounding boxes, and face embeddings
    in a database.
  - Search, in a separate script: take a new image as input, extract its face embeddings,
    search for similar faces by vector similarity, and return the pages that contain images
    of the same character with high confidence.
- Mandated technology MUST NOT be substituted: Python for the implementation, Apache Airflow
  for pipeline orchestration, Docker Compose to run everything (Airflow, the database, and any
  other required service).
- Every deliverable MUST be present: the Airflow pipeline source, a face matching script
  (`face_matcher.py` or similar), a `docker-compose.yml` that runs the entire project, and a
  README covering how to run the pipeline, how to use the face matching script, the
  libraries and models used, the approach and architecture decisions, and example usage with
  expected outputs.
- Scope MUST NOT grow beyond the assignment. Features it does not ask for (UIs, APIs, naming
  people, attribute analysis, model training, extra services) are out of scope unless an
  assignment requirement cannot be met without them, and that need is written down.
- Where the assignment is silent or leaves a choice open, the choice MUST be recorded as a
  decision (in the brief, spec, or README) that cites the assignment text it serves. A
  recorded decision never overrides the assignment.
- Pre-trained models are used as-is; the assignment calls for a pre-trained model, so no
  training or fine-tuning is performed.

Rationale: the assignment is the evaluation contract. Any deviation, omission, or unrequested
addition is a defect against it, regardless of engineering merit.

### II. Typed Code, Checked by ty

- Every function, method, and module-level variable in project Python code (pipeline, DAGs,
  search script, and tests) MUST carry type annotations, including return types (`-> None`
  included).
- `ty check` MUST pass with zero errors on every commit to `main`. New `# type: ignore` or
  `# ty: ignore` comments MUST name the specific rule and give a one-line reason.
- `Any` is permitted only at true trust boundaries (scraped HTML, HTTP responses, DeepFace
  return values, database rows) and MUST be narrowed to a concrete type (dataclass,
  `TypedDict`, `Protocol`) before it crosses into project logic.
- Seams between components (fetcher, image normalizer, face embedder, storage) MUST be typed
  `Protocol`s, so that a non-conforming implementation fails the type check, not a run.

Rationale: the code is read and defended line by line in review; types are the cheapest
documentation that cannot drift, and ty catches contract breaks before a long crawl.

### III. One Linter and Formatter: ruff

- `ruff check` and `ruff format --check` MUST pass on every commit. ruff is the sole linter
  and formatter; black, isort, flake8, and pylint MUST NOT be added.
- Rule selection lives only in `pyproject.toml` under `[tool.ruff]`. Changes to the selected
  rule set are deliberate commits with a stated reason, never incidental.
- Per-line `# noqa` MUST name the rule code.

Rationale: one tool, one config file, zero style debate in review.

### IV. Tests Are the Contract: pytest, offline

- All tests MUST use pytest and live under `tests/`.
- Unit tests MUST run with no network access beyond loopback, no GPU, and no downloaded model
  weights. Websites are replaced by local fixtures; the face model is replaced by a
  deterministic fake embedder behind the embedder `Protocol`. Loopback connections to a
  locally run database are permitted.
- Every behavior change MUST ship with a test that fails without the change. Bug fixes MUST
  include a regression test.
- Each assignment requirement (Principle I) MUST be covered by at least one test that asserts
  its observable outcome: single output format and bounded size, one stored row per detected
  face with bounding box and embedding, page links for every indexed image, and search
  returning only pages above the confidence threshold.
- Tests MUST be deterministic: seeded randomness, no wall-clock or ordering dependence.
- Airflow DAG tests MUST run without a scheduler (DAG import and task-level tests).

Rationale: the assignment's requirements are only met if a test proves them; an untested
requirement is an unverified claim.

### V. Simplicity: KISS, YAGNI, DRY

- KISS: choose the most direct construct that solves the current problem. A plain function
  beats a class; a dataclass beats a metaclass; the standard library beats a dependency.
- YAGNI: build only what the assignment requires (Principle I). An abstraction, option, or
  hook is added only when a concrete current use requires it. The trigger is the second real
  instance, not the anticipated one.
- DRY: each fact (the face model and detector name, the image format and maximum size, the
  similarity metric and threshold, the database schema) has exactly one authoritative
  location, shared by the pipeline and the search script. Duplication of *knowledge* MUST be
  removed; incidental similarity of code that may diverge MAY remain.
- When KISS/YAGNI and DRY conflict, prefer the simpler code and accept small duplication until
  a third occurrence proves the shared shape.

Rationale: a small system is one the author can own and explain end to end, which the
assignment requires ("you are fully responsible for the quality, correctness, and outcomes").

### VI. Structure: SOLID, Law of Demeter, Low Coupling, Convention over Configuration

- Single responsibility: crawling, image normalization, face detection and embedding, storage,
  and search are separate units. Airflow tasks are thin wrappers that call plain, testable
  Python functions; business logic MUST NOT live inside DAG definitions.
- Dependency inversion: core logic depends on protocols and plain data, never directly on
  DeepFace, an HTTP client, or a database driver.
- Law of Demeter: a unit talks only to its direct collaborators. Chains reaching through
  another object's internals MUST be replaced by passing the needed value or asking the owner.
- Coupling and cohesion: modules communicate through typed data and protocols, with no shared
  mutable state and no import cycles; things that change together live together. The search
  script MUST NOT import Airflow.
- Convention over Configuration: sensible defaults (crawl depth, rate limit, maximum image
  size, similarity threshold) live in one place; flags and DAG params exist only to override
  them. New configuration knobs require a current need (see YAGNI).

Rationale: separated units are what make the pipeline testable offline and the search script
independent of the orchestrator, as the assignment's "separate script" requires.

## Quality Gates and Automation

- pre-commit: `.pre-commit-config.yaml` MUST run ruff (check and format) on staged files and ty
  on the whole project. Hook revisions MUST be pinned to exact versions. Contributors MUST
  install the hooks (`pre-commit install`); bypassing them with `--no-verify` is not permitted
  for commits intended for `main`.
- GitHub Actions: `.github/workflows/ci.yml` MUST, on every pull request and push to `main`,
  run (1) all pre-commit hooks on all files, (2) pytest on Python 3.13, and (3) a check that
  `docker compose config` validates the Compose file.
- CI jobs MUST use least-privilege permissions (`contents: read` unless a job needs more),
  pinned action versions, and no secrets.
- A pull request MUST NOT merge with a failing or skipped required check. The local gate and
  the CI gate run the same tools at the same versions, so "passes locally" implies "passes in
  CI."
- Before a deliverable is declared done, the full stack MUST be started with Docker Compose,
  the DAG triggered end to end, and the face matching script run against the resulting
  database; the README's example outputs MUST come from such a run.

## Project Constraints

- Language and runtime: Python 3.13, managed with `uv`, pinned in `uv.lock`; CI installs with
  `uv sync --locked`. Airflow runs in its own Docker image and is never imported by the
  search script or by core (non-DAG) modules.
- Docker Compose is the single entry point: one documented command starts Airflow, the
  database, and every other required service. No service is run outside Compose.
- Shared model contract: the pipeline and the search script MUST use the same face model,
  detector, preprocessing, and distance metric, read from one shared definition. A mismatch
  makes similarities meaningless.
- Result integrity: README example outputs and any reported matches come only from executed
  runs; no simulated or hand-filled results.
- Crawling etiquette: the crawler stays within the target website's domain, obeys its
  robots.txt, and is rate limited.
- Failure isolation: a failure on one page or image MUST NOT fail the whole run; the cause
  MUST be visible in the Airflow task logs.
- Secrets: environment variables only (via a git-ignored `.env`); never committed, never
  printed or logged.

## Governance

- This constitution governs engineering practice in this repository. It is subordinate only to
  `assignment.md`; where anything else (briefs, specs, plans, `CLAUDE.md`, ad-hoc habit)
  conflicts with either, the assignment wins first and this constitution second.
- Amendments are made by a commit or pull request that edits this file, updates the Sync
  Impact Report, and bumps the version. No amendment MAY weaken Principle I. Any amendment
  that introduces a new gate MUST land together with, or be immediately followed by, the
  tooling change that enforces it.
- Versioning (semantic): MAJOR for removing or redefining a principle or gate; MINOR for a new
  principle or section or materially expanded guidance; PATCH for clarifications and wording.
- Compliance: every pull request review MUST check the change against Principles I–VI and the
  Quality Gates. Deviations from Principles II–VI require an explicit, written justification
  in the PR description; deviations from Principle I are not permitted.
- Spec Kit plans (`/speckit-plan`) MUST include a Constitution Check that maps the design to
  the assignment requirements it implements and evaluates it against these principles before
  implementation begins.

**Version**: 1.0.0 | **Ratified**: 2026-11-10 | **Last Amended**: 2026-11-10

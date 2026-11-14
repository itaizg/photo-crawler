<!--
Sync Impact Report
==================
Version change: 1.0.0 → 2.0.0
Bump rationale: MAJOR. Principle II is redefined (records crossing a trust boundary are now
  narrowed to Pydantic models, not dataclasses or TypedDicts), and Governance precedence is
  redefined (the team constitution now ranks equal with this document). Eleven principles
  and two user-requested principles are added on top of that.

Sources:
  - assignment.md (the authority; see Principle I).
  - Team constitution: team-ai-directives `context_modules/constitution.md` (14 principles).
    Adopted with the same weight as this project's own principles.
  - photo-crawler constitution v1.0.0 (Principles I–VI and all sections, preserved).
  - Amendment request of 2026-11-14: Pydantic models over frozen dataclasses, Pythonic syntax,
    avoiding try/except/finally where possible.

Modified principles:
  - II. Typed Code, Checked by ty: trust-boundary narrowing now targets Pydantic models
    (was dataclass / TypedDict / Protocol); Protocols remain for component seams.
  - IV. Tests Are the Contract → IV. Tests Are the Contract and Drive Confidence
    (merged team principle 5: tests verify behavior, not existence).
  - V. Simplicity: KISS, YAGNI, DRY → V. Simplicity First: KISS, YAGNI, DRY
    (merged team principle 11: implementation ladder, protected concerns; Pydantic named as
    the accepted dependency for data records).

Added principles:
  - VII. Pythonic Code (user request)
  - VIII. Exceptions Only at Boundaries (user request)
  - IX. Immutability by Default, with Pydantic Models (team 6 + user request)
  - X. Human Oversight Is Mandatory (team 1)
  - XI. Appropriate Delegation (team 2)
  - XII. Observability and Reproducibility (team 3)
  - XIII. Security by Default (team 4)
  - XIV. Zero Trust (team 9)
  - XV. Stateless, Idempotent Tasks (team 8)
  - XVI. Documentation Matters (team 7)
  - XVII. Think Before Coding (team 10)
  - XVIII. Surgical Changes (team 12)
  - XIX. Goal-Driven Execution (team 13)
  - XX. Memory: Learn From Every Run (team 14)
  Team principles 5 and 11 are merged into IV and V rather than duplicated.

Added sections:
  - "Inheritance" preamble under Core Principles.
Modified sections:
  - Quality Gates and Automation: ruff rule families enforcing VII and VIII.
  - Project Constraints: Pydantic as a core dependency; read-only search DB role.
  - Governance: precedence (assignment > team constitution = this constitution), conflict
    rule between equal-ranked principles, compliance range I–XX.
Removed sections: none.

Follow-up TODOs (repository changes, not made by this amendment):
  - Add pyproject.toml with [tool.ruff] (including the rule families listed under Quality
    Gates) and [tool.ty], .pre-commit-config.yaml, and .github/workflows/ci.yml so that the
    Quality Gates are enforced. (Carried over from 1.0.0, extended.)
  - Add pydantic to the project dependencies when pyproject.toml is created.
-->

# photo-crawler Constitution

## Core Principles

**Inheritance.** This constitution inherits the team constitution of team-ai-directives
(`context_modules/constitution.md`) and carries it at the same weight as this project's own
principles. Every team principle appears below, either as its own principle or merged into the
project principle that already covers it (team 5 → IV, team 11 → V). Both documents rank below
`assignment.md` (Principle I); no team principle may widen the assignment's scope.

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
  return values, database rows) and MUST be narrowed to a Pydantic model (Principle IX)
  before it crosses into project logic.
- Seams between components (fetcher, image normalizer, face embedder, storage) MUST be typed
  `Protocol`s, so that a non-conforming implementation fails the type check, not a run.
- Modern typing syntax for Python 3.13 MUST be used: `list[str]`, `X | None`, the `type`
  statement for aliases, and PEP 695 generics; `typing.List`, `Optional`, and `Union` MUST NOT
  be used.

Rationale: the code is read and defended line by line in review; types are the cheapest
documentation that cannot drift, and ty catches contract breaks before a long crawl.

### III. One Linter and Formatter: ruff

- `ruff check` and `ruff format --check` MUST pass on every commit. ruff is the sole linter
  and formatter; black, isort, flake8, and pylint MUST NOT be added.
- Rule selection lives only in `pyproject.toml` under `[tool.ruff]`. Changes to the selected
  rule set are deliberate commits with a stated reason, never incidental.
- Per-line `# noqa` MUST name the rule code.

Rationale: one tool, one config file, zero style debate in review.

### IV. Tests Are the Contract and Drive Confidence

- All tests MUST use pytest and live under `tests/`.
- Unit tests MUST run with no network access beyond loopback, no GPU, and no downloaded model
  weights. Websites are replaced by local fixtures; the face model is replaced by a
  deterministic fake embedder behind the embedder `Protocol`. Loopback connections to a
  locally run database are permitted.
- Tests are written before or alongside the logic they cover. Every behavior change MUST ship
  with a test that fails without the change. Bug fixes MUST include a regression test.
- Tests MUST assert observable behavior, not existence. A test that would still pass if the
  feature were broken (asserting only that a function returns, that an object is not `None`,
  or that a mock was called) counts as a missing test.
- Each assignment requirement (Principle I) MUST be covered by at least one test that asserts
  its observable outcome: single output format and bounded size, one stored row per detected
  face with bounding box and embedding, page links for every indexed image, and search
  returning only pages above the confidence threshold.
- Tests MUST be deterministic: seeded randomness, no wall-clock or ordering dependence.
- Airflow DAG tests MUST run without a scheduler (DAG import and task-level tests).
- A change MUST NOT ship when coverage of a requirement it touches is missing, or when its
  tests pass without exercising the behavior.

Rationale: the assignment's requirements are only met if a test proves them; an untested
requirement is an unverified claim, and a hollow test is worse because it hides that.

### V. Simplicity First: KISS, YAGNI, DRY

- KISS: choose the most direct construct that solves the current problem. A plain function
  beats a class; the standard library beats a dependency. Pydantic is the one accepted
  exception for data records (Principle IX).
- YAGNI: build only what the assignment requires (Principle I). An abstraction, option, or
  hook is added only when a concrete current use requires it. The trigger is the second real
  instance, not the anticipated one.
- Implementation ladder: before writing code, stop at the first rung that holds:
  1. Does this need to exist? If not, skip it.
  2. Is there a built-in language or platform capability? Use it.
  3. Is there a native framework or runtime feature (Airflow, Pydantic, DeepFace, the
     database)? Use it.
  4. Is there an already available dependency? Use it.
  5. Is it one line? Write one line.
  6. Only then write the minimum that works.
- DRY: each fact (the face model and detector name, the image format and maximum size, the
  similarity metric and threshold, the database schema) has exactly one authoritative
  location, shared by the pipeline and the search script. Duplication of *knowledge* MUST be
  removed; incidental similarity of code that may diverge MAY remain.
- When KISS/YAGNI and DRY conflict, prefer the simpler code and accept small duplication until
  a third occurrence proves the shared shape.
- Trust-boundary validation, data-loss handling, security, and failure visibility are never
  removed in the name of simplicity.

Rationale: a small system is one the author can own and explain end to end, which the
assignment requires ("you are fully responsible for the quality, correctness, and outcomes").

### VI. Structure: SOLID, Law of Demeter, Low Coupling, Convention over Configuration

- Single responsibility: crawling, image normalization, face detection and embedding, storage,
  and search are separate units. Airflow tasks are thin wrappers that call plain, testable
  Python functions; business logic MUST NOT live inside DAG definitions.
- Dependency inversion: core logic depends on protocols and plain data, never directly on
  DeepFace, an HTTP client, or a database driver. Collaborators are passed in (constructor or
  function arguments), not created inside the logic that uses them.
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

### VII. Pythonic Code

- Code MUST use the idiomatic Python construct where one exists:
  - comprehensions and generator expressions instead of loops that append or build dicts;
  - `pathlib.Path` instead of `os.path` and string path joins;
  - f-strings instead of `%` or `str.format`;
  - `with` statements for every resource (files, HTTP sessions, DB connections, images);
  - `enumerate`, `zip(..., strict=True)`, tuple unpacking, and `itertools` / `functools`
    instead of index arithmetic;
  - generators for streams of unbounded or large size (crawled pages, image batches);
  - truthiness checks (`if items:`) and `is None` / `is not None` comparisons;
  - `match` statements where they make branching on shape clearer than `if` chains;
  - keyword-only parameters (`*`) for configuration-like arguments.
- Java-style patterns MUST NOT be used: no getter/setter methods (use attributes, properties,
  or model fields), no classes holding only static methods (use module functions), no
  manual `__init__` boilerplate for records (use Pydantic models).
- Readability wins ties: a one-line comprehension that needs a comment to be understood
  MUST be rewritten as a plain loop or a named helper.

Rationale: idiomatic code is shorter, matches what every Python reviewer expects, and lets
ruff catch the non-idiomatic forms automatically.

### VIII. Exceptions Only at Boundaries

- `try`/`except`/`finally` MUST be avoided where another construct does the job:
  - `with` statements and context managers replace `finally` for cleanup; `finally` MUST NOT
    be used for resource release;
  - Pydantic validation and explicit checks replace catching errors from bad data;
  - functions return `X | None` (or an empty collection) for expected "nothing found"
    outcomes instead of raising and catching;
  - library options that report outcomes instead of raising are preferred (for example,
    DeepFace with `enforce_detection=False`, then filtering on the face confidence it
    reports; HTTP responses checked by status instead of catching raised errors).
- Where an exception cannot be avoided, it MAY be caught only at a trust boundary: network
  fetch, image decoding, model inference, database I/O, and the per-item failure-isolation
  point of a pipeline task (Project Constraints, "Failure isolation"). There:
  - the narrowest exception type MUST be caught; bare `except:` is forbidden, and
    `except Exception` is permitted only at the single per-item isolation point of a task;
  - the `try` body MUST contain only the call that can raise;
  - the exception MUST NOT be swallowed: it is logged with its traceback and counted
    (Principle XII), or re-raised with `raise ... from err`;
  - `contextlib.suppress` counts as exception handling and follows the same rules.
- Exceptions MUST NOT be used for ordinary control flow.

Rationale: every `try` is a place where a failure can disappear. Keeping them few, narrow, and
at the edges makes the remaining failures loud and easy to trace.

### IX. Immutability by Default, with Pydantic Models

- Data records that cross a function or component boundary (page, image, face, embedding,
  search match, settings) MUST be Pydantic v2 models declared with
  `model_config = ConfigDict(frozen=True)`. Frozen dataclasses, `NamedTuple`, `TypedDict`,
  and plain dicts MUST NOT be used for such records.
- Values from trust boundaries (Principle II) are parsed into these models with
  `Model.model_validate(...)`, so invalid data is rejected at the edge, once.
- Functions MUST return new objects instead of mutating their inputs; changes to a record use
  `model.model_copy(update={...})`. Fields hold immutable types (`tuple`, `frozenset`,
  nested frozen models); embeddings are stored on models as `tuple[float, ...]`.
- Settings are a single frozen Pydantic settings model, the one place defaults live
  (Principle VI, Convention over Configuration).
- Module-level mutable state is forbidden; constants are immutable.

Rationale: frozen, validated records make every function predictable and every boundary
checked once; Pydantic gives validation, serialization, and immutability in one construct.

### X. Human Oversight Is Mandatory

- Every change produced by an AI agent MUST be reviewed by a human before it merges into
  `main`. An agent MUST NOT merge or approve its own pull request.
- Agents work inside the guardrails of this constitution and the Quality Gates; the human
  author is accountable for every merged line, as the assignment states.

Rationale: autonomy speeds up the work, but accountability for the deliverable stays with a
person.

### XI. Appropriate Delegation

- Deterministic decisions MUST be expressed as deterministic code or configuration: the
  similarity threshold, the distance metric, crawl rules, retry counts, and image limits are
  values in code, never judgments delegated to a model at runtime.
- The pre-trained face model is used only for what requires it (face detection and
  embedding). No LLM calls are part of the pipeline or the search script.
- During development, AI agents MUST NOT invent such values ad hoc; each comes from a recorded
  decision (Principle I).

Rationale: a rule that a model re-decides on every call gives different answers on different
days; a rule in code gives the same answer every time.

### XII. Observability and Reproducibility

- All runtime code MUST log through the standard `logging` module (which Airflow captures);
  `print` MUST NOT be used outside the search script's user-facing output.
- Every pipeline task MUST log, at the end of its run, counts of what it processed,
  succeeded, skipped, and failed (for example: pages crawled, images downloaded, images
  without faces, faces stored).
- Skips, fallbacks, and failures MUST be surfaced in those counts and logs. A task MUST NOT
  report success while silently dropping work; a task whose every item failed MUST fail.
- Runs are reproducible: the same input and configuration produce the same stored results.
  Model, detector, and dependency versions are pinned.
- Within the assignment's scope this means logs and counts; no metrics or tracing service is
  added (Principle I).

Rationale: silent successes that hide silent failures are the most expensive class of bug;
counts in the task log make them visible on the first run.

### XIII. Security by Default

- All external input MUST be validated before use: scraped HTML and URLs, downloaded image
  bytes (content type, decoded dimensions, Pillow's decompression-bomb limit), and the image
  passed to the search script.
- Database access MUST use parameterized queries; SQL MUST NOT be built by string
  formatting.
- Credentials follow least privilege, are provided as managed secrets (environment
  variables from a git-ignored `.env`), and are never hard-coded, committed, printed, or
  logged.
- Dependencies are pinned and updated deliberately.

Rationale: a crawler ingests untrusted content by design; every input is hostile until
validated.

### XIV. Zero Trust

- No service is trusted because it is inside the Compose network: the database requires
  authentication for every connection, and the Airflow web server requires login with
  non-default credentials.
- Each component gets only the access it needs: the pipeline writes through its own database
  role; the search script connects with a read-only role.
- Default credentials shipped by images (for example Airflow's) MUST be replaced through the
  `.env` file.

Rationale: a compromised or misconfigured container should not be able to reach more than
its own job requires.

### XV. Stateless, Idempotent Tasks

- Airflow tasks and the search script MUST NOT keep state between runs in memory or on worker
  local disk. State lives in the database or in a declared Compose volume.
- Data passed between tasks goes through the database or the volume; XCom carries only small
  references (IDs, counts, paths).
- Tasks MUST be idempotent: re-running a task or a whole DAG run MUST NOT create duplicate
  rows (records are keyed, for example by image URL or content hash, and written with
  upsert semantics).

Rationale: stateless, idempotent tasks can be retried, re-run, and scaled by Airflow without
corrupting the index.

### XVI. Documentation Matters

- Assumptions, decisions, and data contracts (the Pydantic models, the database schema, the
  embedding format and dimension) MUST be documented in the repository, next to the code or in
  the README / spec.
- Public modules, classes, and functions MUST have docstrings stating purpose and any
  non-obvious behavior; comments explain *why*, not *what*.
- Work handed between sessions or people leaves hand-off notes in the feature's spec
  directory.

Rationale: agents and humans both move fast only when the context is written down where they
will look for it.

### XVII. Think Before Coding

- Assumptions MUST be stated explicitly. When a requirement is ambiguous, ask instead of
  guessing; when several interpretations exist, present them.
- Read before writing: understand adjacent code and nearby files before adding code.
- When the codebase disagrees with itself, flag the conflict and pick one side; MUST NOT
  produce code that half-satisfies both.
- Push back when a simpler approach exists; stop and name what is unclear when confused.

Rationale: most wasted work starts from an unstated assumption.

### XVIII. Surgical Changes

- A change touches only what its task requires. Adjacent code, comments, and formatting are
  not "improved" in passing, and working code is not refactored without a task for it.
- New code matches the existing style and patterns, even where another pattern would be
  preferred; a second pattern for the same thing MUST NOT be introduced.
- A change removes the imports, variables, and functions that it made unused, and nothing
  else.

Rationale: small, focused diffs are reviewable (Principle X) and keep unrelated breakage out.

### XIX. Goal-Driven Execution

- Every task MUST have explicit, verifiable success criteria before work starts (spec-kit
  specs and tasks provide them).
- Multi-step work states a short plan with how each step is verified, then loops until the
  criteria are met.
- After each significant step of long-running work, a checkpoint summarizes what was done
  and what was verified before continuing.

Rationale: strong success criteria let work proceed autonomously; weak ones demand constant
clarification.

### XX. Memory: Learn From Every Run

- Notable failures, surprises, and decisions from development and pipeline runs MUST be
  recorded so they are reused: in the feature's spec directory, and, when they generalize
  beyond this project, proposed to team-ai-directives through `/team-curate`.
- Recorded lessons are consolidated, not just appended: a new lesson that contradicts an old
  one replaces it.
- This principle governs the development process; it does not add runtime components
  (Principle I).

Rationale: without memory, every session starts from scratch; with it, the work compounds.

## Quality Gates and Automation

- pre-commit: `.pre-commit-config.yaml` MUST run ruff (check and format) on staged files and ty
  on the whole project. Hook revisions MUST be pinned to exact versions. Contributors MUST
  install the hooks (`pre-commit install`); bypassing them with `--no-verify` is not permitted
  for commits intended for `main`.
- ruff rule selection MUST include the families that enforce Principles VII and VIII, at
  minimum: `UP` (pyupgrade), `SIM` (simplify), `C4` (comprehensions), `PTH` (pathlib), `RET`
  (returns), `PIE`, `PERF`, `E722` and `BLE` (bare / blind except), and `TRY` (exception
  handling patterns).
- GitHub Actions: `.github/workflows/ci.yml` MUST, on every pull request and push to `main`,
  run (1) all pre-commit hooks on all files, (2) pytest on Python 3.13, and (3) a check that
  `docker compose config` validates the Compose file.
- CI jobs MUST use least-privilege permissions (`contents: read` unless a job needs more),
  pinned action versions, and no secrets.
- A pull request MUST NOT merge with a failing or skipped required check, and MUST NOT merge
  without human review (Principle X). The local gate and the CI gate run the same tools at
  the same versions, so "passes locally" implies "passes in CI."
- Before a deliverable is declared done, the full stack MUST be started with Docker Compose,
  the DAG triggered end to end, and the face matching script run against the resulting
  database; the README's example outputs MUST come from such a run.

## Project Constraints

- Language and runtime: Python 3.13, managed with `uv`, pinned in `uv.lock`; CI installs with
  `uv sync --locked`. Airflow runs in its own Docker image and is never imported by the
  search script or by core (non-DAG) modules.
- Core libraries: Pydantic v2 for data records and settings (Principle IX).
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
  MUST be visible in the Airflow task logs and in the task's failure count (Principle XII).
- Secrets: environment variables only (via a git-ignored `.env`); never committed, never
  printed or logged. The search script uses a read-only database role (Principle XIV).

## Governance

- Precedence: `assignment.md` first. Below it, the team constitution (team-ai-directives
  `context_modules/constitution.md`) and this constitution rank equally. Everything else
  (briefs, specs, plans, `CLAUDE.md`, ad-hoc habit) ranks below both.
- Conflicts between equally ranked principles: apply both where possible; otherwise the
  stricter requirement wins. Where neither is stricter, the choice MUST be recorded as a
  decision in the PR description. No principle may widen the assignment's scope.
- Team constitution changes: when the team constitution changes, this document MUST be
  re-synced through `/speckit-constitution` so the inheritance stays complete.
- Amendments are made by a commit or pull request that edits this file, updates the Sync
  Impact Report, and bumps the version. No amendment MAY weaken Principle I. Any amendment
  that introduces a new gate MUST land together with, or be immediately followed by, the
  tooling change that enforces it.
- Versioning (semantic): MAJOR for removing or redefining a principle or gate; MINOR for a new
  principle or section or materially expanded guidance; PATCH for clarifications and wording.
- Compliance: every pull request review MUST check the change against Principles I–XX and the
  Quality Gates. Deviations from Principles II–XX require an explicit, written justification
  in the PR description; deviations from Principle I are not permitted.
- Spec Kit plans (`/speckit-plan`) MUST include a Constitution Check that maps the design to
  the assignment requirements it implements and evaluates it against these principles before
  implementation begins.

**Version**: 2.0.0 | **Ratified**: 2026-11-10 | **Last Amended**: 2026-11-14

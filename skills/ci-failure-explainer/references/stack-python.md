# Python CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`. A backtick inside a log line is written as `.`.

Quick orientation: check the Python version (`actions/setup-python` vs `.python-version` / `requires-python`), the installer (pip, uv, poetry, pdm, pipenv) and **how** the project is installed (`pip install -e .` vs requirements only). Most "works on my machine" failures come from a local virtualenv that has packages CI never installs.

### PY-01 · Dependency resolution conflict
- **Signature:** `ResolutionImpossible` · `Cannot install .+ because these package versions have conflicting dependencies` · `SolverProblemError` · `No solution found when resolving dependencies`
- **Category:** dependency
- **Cause:** Two requirements pin incompatible versions of a shared dependency. Often triggered by an unpinned dependency releasing a new major.
- **Fix:** Read the conflict chain, then relax or align the named constraint. Use a lockfile (`uv.lock`, `poetry.lock`, or `pip-compile` output) so CI installs exactly what you tested.
- **Verify:** `pip install -r requirements.txt` (or `uv sync --locked` / `poetry install --sync`)

### PY-02 · No matching distribution / wrong Python version
- **Signature:** `No matching distribution found for` · `Could not find a version that satisfies the requirement` · `requires a different Python: [\d.]+ not in` · `Requires-Python [>=<!~][^ ]+`
- **Category:** config
- **Cause:** The package doesn't support CI's Python version (too new: no wheels yet; too old: dropped). Also a typo in the package name, or a private index CI can't reach.
- **Fix:** Align `actions/setup-python` `python-version` with `requires-python` and test a matrix of the versions you support. Pin the package to a version with wheels for that Python.
- **Verify:** `python --version && pip install <pkg>==<ver>`

### PY-03 · `ModuleNotFoundError` in CI
- **Signature:** `ModuleNotFoundError: No module named '[\w.]+'` · `ImportError: cannot import name '\w+' from`
- **Category:** dependency
- **Cause:** The module is in your local venv but not in the declared dependencies (often a test-only dependency missing from `requirements-dev.txt` / `[project.optional-dependencies]`), or an upgrade removed or renamed the import.
- **Fix:** Add the package to the right dependency list (e.g. `httpx` under `[project.optional-dependencies] test` or `requirements-dev.txt`), and make sure CI installs that group: `pip install -e ".[test]"`. To find undeclared imports, create a fresh venv locally and run the tests there.
- **Verify:** `python -m venv /tmp/v && /tmp/v/bin/pip install -e ".[test]" && /tmp/v/bin/pytest -x`

### PY-04 · Project itself not importable (src layout / not installed)
- **Signature:** `No module named '(?:src|app|tests)(?:\.\w+)*'` · `attempted relative import with no known parent package`
- **Category:** config
- **Cause:** Locally the project root is on `sys.path` (IDE, `PYTHONPATH`), but in CI it isn't installed. Typical with `src/` layouts.
- **Fix:** Install the project in CI (`pip install -e .`) or configure pytest: `[tool.pytest.ini_options] pythonpath = ["src"]`. Avoid `sys.path` hacks in tests.
- **Verify:** `pip install -e . && pytest -x`

### PY-05 · pytest collection errors
- **Signature:** `ERROR collecting \S+` · `Interrupted: \d+ errors? during collection` · `ImportError while importing test module`
- **Category:** test
- **Cause:** pytest couldn't even import a test module. Usually an import error (PY-03/PY-04) or a syntax error, sometimes duplicate test file basenames without `__init__.py`.
- **Fix:** Fix the underlying import or syntax error shown under the collection error. For duplicate basenames, add `__init__.py` or use `--import-mode=importlib`.
- **Verify:** `pytest --collect-only -q`

### PY-06 · Fixture problems and test order dependence
- **Signature:** `fixture '\w+' not found` · `ScopeMismatch: You tried to access the .+ scoped fixture` · `Using --randomly-seed=\d+`
- **Category:** test
- **Cause:** A fixture is defined in a `conftest.py` that CI doesn't load (different rootdir), has the wrong scope, or tests share mutable state and only pass in a particular order (pytest-randomly or xdist expose this).
- **Fix:** Move shared fixtures to the right `conftest.py`. Isolate state (fresh DB or tmp dirs per test, `monkeypatch` instead of globals). Reproduce order issues with `pytest -p randomly --randomly-seed=<seed>`.
- **Verify:** `pytest -p no:randomly && pytest -p randomly --randomly-seed=<seed from log>`

### PY-07 · mypy type errors
- **Signature:** `^\S+\.py:\d+: error: .+\[[\w-]+\]$` · `Found \d+ errors? in \d+ files? \(checked \d+ source files?\)`
- **Category:** lint
- **Cause:** Type errors, or CI's mypy or stub versions differ from local (`types-*` packages missing, a new mypy release).
- **Fix:** Pin mypy and the stub packages in dev requirements. Run the CI command locally. Use a targeted `# type: ignore[code]` only with a reason.
- **Verify:** `mypy .` (same args as CI)

### PY-08 · ruff / flake8 lint errors
- **Signature:** `^\S+\.py:\d+:\d+: [A-Z]{1,3}\d{3,4} ` · `^Found \d+ errors?\.$` · `\[\*\] \d+ fixable with the .--fix. option`
- **Category:** lint
- **Cause:** Lint violations. A ruff upgrade often enables new rules when you select whole categories.
- **Fix:** `ruff check --fix .` and fix the rest. Pin ruff (in pre-commit and dev requirements) to the same version CI uses.
- **Verify:** `ruff check .`

### PY-09 · black / ruff format check
- **Signature:** `^would reformat ` · `Oh no! 💥 💔 💥` · `\d+ files? would be reformatted` · `^Would reformat: `
- **Category:** lint
- **Cause:** Files aren't formatted, or the formatter version differs (black's yearly style changes).
- **Fix:** Run `black .` / `ruff format .` with the pinned version and commit.
- **Verify:** `black --check .` or `ruff format --check .`

### PY-10 · Wheel fails to build from source
- **Signature:** `Failed building wheel for \S+` · `error: subprocess-exited-with-error` · `fatal error: Python\.h: No such file or directory` · `Microsoft Visual C\+\+ 14\.0 or greater is required`
- **Category:** dependency
- **Cause:** No prebuilt wheel exists for this Python, OS or arch (common right after a new Python release or on `macos-14` arm64), so pip compiles C or Rust extensions and lacks headers or compilers.
- **Fix:** Use a Python version that has wheels, upgrade the package, or install build deps (`sudo apt-get install -y python3-dev build-essential libpq-dev`). Use `pip install --only-binary=:all:` to surface the issue early.
- **Verify:** `pip install --only-binary=:all: <pkg>`

### PY-11 · Tests rely on local files or paths
- **Signature:** `FileNotFoundError: \[Errno 2\] No such file or directory: '[^']+'` · `PermissionError: \[Errno 13\]`
- **Category:** test
- **Cause:** Tests read files that exist only locally (git-ignored fixtures, `.env`, absolute home paths), or depend on the working directory.
- **Fix:** Commit small fixtures, and build paths from the test file: `Path(__file__).parent / "data" / "x.json"`. Use `tmp_path` for written files.
- **Verify:** `cd / && pytest /path/to/repo/tests -x`

### PY-12 · Timezone / locale / clock dependence
- **Signature:** `can't compare offset-naive and offset-aware datetimes` · `ZoneInfoNotFoundError` · `UnknownTimeZoneError` · `UnicodeDecodeError: 'ascii' codec can't decode`
- **Category:** flaky
- **Cause:** Runners use UTC and the C/POSIX locale. Local machines don't. Tests using `datetime.now()` / `date.today()` fail near midnight, at month ends or across DST. `tzdata` is missing in slim images.
- **Fix:** Use timezone-aware datetimes, freeze time (`freezegun`, `time-machine`), add `tzdata` as a dependency, and open files with `encoding="utf-8"`.
- **Verify:** `TZ=UTC LANG=C pytest -x` and `TZ=Pacific/Kiritimati pytest -x`

### PY-13 · Lockfile out of date (poetry / uv / pipenv)
- **Signature:** `pyproject\.toml changed significantly since poetry\.lock was last generated` · `poetry\.lock is not consistent with pyproject\.toml` · `The lockfile at .uv\.lock. needs to be updated` · `Pipfile\.lock \([0-9a-f]+\) out of date`
- **Category:** dependency
- **Cause:** `pyproject.toml` changed without re-locking.
- **Fix:** `poetry lock` / `uv lock` / `pipenv lock`, then commit the lockfile.
- **Verify:** `poetry check --lock` or `uv lock --check`

### PY-14 · `externally-managed-environment` (PEP 668)
- **Signature:** `error: externally-managed-environment`
- **Category:** config
- **Cause:** Newer Ubuntu or Debian images block `pip install` into the system Python.
- **Fix:** Use `actions/setup-python` (its Python isn't externally managed), or a venv: `python3 -m venv .venv && . .venv/bin/activate`. Avoid `--break-system-packages` in CI.
- **Verify:** `python -m pip install -e .`

### PY-15 · Coverage threshold not reached
- **Signature:** `FAIL Required test coverage of \d+(?:\.\d+)?% not reached` · `Coverage failure: total of [\d.]+ is less than fail-under`
- **Category:** test
- **Cause:** New code isn't covered by tests, or tests were skipped in CI (missing env or markers), which lowers total coverage.
- **Fix:** Add tests for the new code, or find skipped tests (`pytest -rs`). Lower `fail_under` only with team agreement.
- **Verify:** `pytest --cov --cov-report=term-missing`

### PY-16 · `distutils` / `pkg_resources` removed (Python 3.12+)
- **Signature:** `No module named 'distutils'` · `No module named 'pkg_resources'` · `module 'pkgutil' has no attribute 'ImpImporter'`
- **Category:** dependency
- **Cause:** Python 3.12 removed `distutils`, and new virtualenvs don't preinstall setuptools. Old packages (or old pinned versions) break.
- **Fix:** Upgrade the offending package (the traceback shows which). As a stopgap, add `setuptools` to the dependencies.
- **Verify:** `python -c "import <pkg>" && pytest -x`

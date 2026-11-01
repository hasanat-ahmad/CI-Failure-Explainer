# Flaky tests: recognise, confirm, fix properly

A flaky test passes and fails on the **same code**. Treat "it's flaky" as a hypothesis that needs evidence, not as a diagnosis, because real bugs (races, time-zone logic) often look flaky.

## Recognise: signals in order of strength

1. **The same commit passed before.** `ci_doctor.py` / `fetch_failed_logs.py` report `same_sha_succeeded`, the attempt number, and a history strip (`✓✗✓✓✗` alternating is a strong signal).
2. **Only a rerun differs.** Attempt 2 passed with no change.
3. **The error is about time or the outside world:** timeouts, `ECONNRESET`, DNS, 5xx from a third-party API, "Timed out waiting for".
4. **The failing test is unrelated to the diff.** It touches code the PR didn't change.
5. **The failure moves around.** A different test fails each time, or it only fails in parallel or in one matrix entry.

Common root causes: timing/sleep-based assertions, shared state between tests, test-order dependence, real network calls, current date/time/timezone, random data or seeds, parallelism (ports, DB rows, temp files), and runner resource limits (slower CPU, less RAM than laptops).

## Confirm before calling it flaky

| Check | Command |
|---|---|
| Rerun only the failed jobs | `gh run rerun <run-id> --failed` *(ask the user first)* |
| Run the test N times locally | Jest `npx jest -t "<name>" --runInBand` in a loop · pytest `pytest -k <name> --count=50` (pytest-repeat) · Go `go test -run '^TestX$' -count=50` · Flutter `for i in $(seq 20); do flutter test --plain-name "<name>" || break; done` |
| Random order | pytest `-p randomly` · Jest `--randomize` · Go `-shuffle=on` · RSpec `--order random` |
| Isolation vs together | Run the test alone, then with its whole file or suite, and compare |
| Under load (simulate a slow runner) | Run with parallelism (`-n auto`, `--maxWorkers=50%`), or use `stress`/`nice` locally |
| Timezone / clock | `TZ=UTC`, `TZ=Asia/Kolkata`, `TZ=Pacific/Kiritimati`, and a date near month or year end |

Report the confirmation result: "failed 3/50 locally under `--randomize`" is evidence, "probably flaky" is not.

## Fix properly (no blind retries)

- **Retries** (`jest.retryTimes`, `pytest-rerunfailures`, `retry:` in CI) hide the problem and slow every run. Only add them for genuine external flakiness you can't control, scoped to that test, with a linked issue.
- Quarantining (skip + issue) is acceptable **only** when the user agrees. Never silently skip tests to make CI green.

## Known flaky patterns

### FLAKY-01 · Real network calls in tests
- **Signature:** `\bE(?:CONNRESET|TIMEDOUT|CONNREFUSED|AI_AGAIN)\b` · `socket hang up` · `Exceeded timeout of \d+ ?ms for a test` · `(?:ReadTimeout|ConnectTimeout|requests\.exceptions\.ConnectionError|httpx\.ConnectError)`
- **Category:** flaky
- **Cause:** The test calls a real external service (third-party API, staging server, package registry). Latency and outages on shared runners make it time out or reset intermittently.
- **Fix:** Mock at the HTTP boundary (`msw`, `nock`, `responses`, `respx`, `httptest`, `mockito`), or use recorded fixtures (VCR / Polly). Keep real-service checks in a separate, non-blocking contract-test job.
- **Verify:** Disable network and run: `npx jest <file>` with nock `disableNetConnect()` / `pytest --disable-socket` (pytest-socket)

### FLAKY-02 · Timing / sleep-based assertions
- **Signature:** `Timed out retrying after \d+ ?ms` · `waitFor(?:Element)?.* timed? ?out` · `TimeoutError: (?:Waiting for|page\.\w+: Timeout \d+ms exceeded)` · `pumpAndSettle timed out`
- **Category:** flaky
- **Cause:** The test sleeps a fixed time or expects something within a deadline that's too tight for a slower runner (UI renders, async jobs, animations).
- **Fix:** Wait for a condition instead of a duration (`await expect(locator).toBeVisible()`, `waitFor`, polling with timeout). Use fake timers for time-driven logic.
- **Verify:** Run 20× with CPU throttled or under parallel load

### FLAKY-03 · Test order dependence / shared state
- **Signature:** `Using --randomly-seed=\d+` · `Randomized with seed \d+` · `UNIQUE constraint failed` · `duplicate key value violates unique constraint`
- **Category:** flaky
- **Cause:** Tests share mutable state (globals, singletons, DB rows, files, env vars, module caches) and pass only in one order.
- **Fix:** Reset state per test (transactions rolled back, fresh temp dirs, `monkeypatch`, `jest.resetModules()`), and generate unique IDs per test.
- **Verify:** Rerun with the seed from the log (`pytest -p randomly --randomly-seed=<seed>`, `jest --randomize --seed=<seed>`)

### FLAKY-04 · Date / time / timezone dependence
- **Signature:** `can't compare offset-naive and offset-aware datetimes` · `(?:toLocaleDateString|toLocaleString|Intl\.DateTimeFormat)` · `Expected: "?\d{4}-\d{2}-\d{2}` · `\bDST\b`
- **Category:** flaky
- **Cause:** Results depend on "now", the runner timezone (UTC) or locale. Tests fail near midnight, at month or year boundaries, or across DST changes.
- **Fix:** Inject a clock or freeze time (`jest.useFakeTimers().setSystemTime()`, `freezegun`, `clock` package in Dart), and pin `TZ` in the test setup.
- **Verify:** `TZ=Pacific/Kiritimati <test cmd>` and with the clock frozen to `2024-02-29T23:59:59Z`

### FLAKY-05 · Randomised data without a fixed seed
- **Signature:** `Falsifying example:` · `(?:faker|Faker)\.` · `Hypothesis` · `seed=\d+`
- **Category:** flaky
- **Cause:** Random test data sometimes hits an edge case (empty string, unicode, duplicate). Property-based tools find real bugs this way too.
- **Fix:** Log and fix the seed so failures reproduce, then turn the failing example into a permanent regression test. Fix the bug if it's real.
- **Verify:** Rerun with the logged seed / `@example(...)`

### FLAKY-06 · Parallelism and resource contention
- **Signature:** `EADDRINUSE` · `address already in use` · `database is locked` · `deadlock detected`
- **Category:** flaky
- **Cause:** Parallel workers share ports, databases, or files.
- **Fix:** Use port 0 (OS-assigned), a DB or schema per worker (`JEST_WORKER_ID`, `PYTEST_XDIST_WORKER`), and `tmp_path` / `mkdtemp`.
- **Verify:** Run with max parallelism several times

### FLAKY-07 · Leaked async work after tests finish
- **Signature:** `Cannot log after tests are done` · `You are trying to .import. a file after the Jest environment has been torn down` · `This usually means that there are asynchronous operations that weren't stopped`
- **Category:** flaky
- **Cause:** Un-awaited promises, timers or listeners outlive the test and fail later tests at random.
- **Fix:** `await` every async call, clean up in `afterEach`, use fake timers, and close servers and clients.
- **Verify:** `npx jest --detectOpenHandles --runInBand`

### FLAKY-08 · Runner resource limits
- **Signature:** `Timed out waiting for the emulator` · `emulator: ERROR: .*(?:timed? ?out|not respond)` · `Test took too long` · `slow test`
- **Category:** flaky
- **Cause:** Emulators, browsers and heavy suites need more CPU or RAM than shared runners reliably provide.
- **Fix:** Reduce suite parallelism, enable hardware acceleration (KVM for Android emulators), split into shards, or use larger runners for that job.
- **Verify:** Compare step durations across recent runs (`gh run view <id> --json jobs`)

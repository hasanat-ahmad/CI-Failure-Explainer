# Go CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`.

Quick orientation: compare `actions/setup-go` `go-version` with the `go` / `toolchain` lines in `go.mod`, check for CGO and OS-specific code, and remember `go test` runs packages in parallel.

### GO-01 · `go.mod` / `go.sum` out of date
- **Signature:** `missing go\.sum entry for module providing package` · `updates to go\.mod needed` · `go: updates to go\.sum needed` · `verifying \S+: checksum mismatch`
- **Category:** dependency
- **Cause:** Imports changed without running `go mod tidy`, or `go.sum` wasn't committed. A checksum mismatch means a retagged module or a tampered proxy cache.
- **Fix:** `go mod tidy` and commit `go.mod` + `go.sum`. Add a CI check: `go mod tidy && git diff --exit-code`.
- **Verify:** `go mod verify && go build ./...`

### GO-02 · Go version too old for `go.mod`
- **Signature:** `go\.mod requires go >= \S+` · `go: errors parsing go\.mod` · `invalid go version '\S+': must match format` · `toolchain not available`
- **Category:** config
- **Cause:** `go.mod` declares `go 1.22` (or a `toolchain` line), but CI installs an older Go, often because `go-version` is hard-coded.
- **Fix:** `uses: actions/setup-go@v5` with `go-version-file: go.mod`.
- **Verify:** `go version && go build ./...`

### GO-03 · Compile error
- **Signature:** `^\S+\.go:\d+:\d+: (?:undefined|cannot use|too many|not enough|imported and not used|declared and not used)` · `^# \S+$`
- **Category:** build
- **Cause:** Real compile errors, often from a dependency API change, or a file excluded by build tags on the CI OS (`//go:build linux`).
- **Fix:** Go to `file:line:col`. If it's a dependency, check its release notes and pin with `go get module@version`.
- **Verify:** `go build ./... && go vet ./...`

### GO-04 · Test failure
- **Signature:** `^--- FAIL: \S+` · `^FAIL\s+\S+\s+[\d.]+s$` · `^\s+\S+_test\.go:\d+: `
- **Category:** test
- **Cause:** A failing assertion. The `_test.go:line` line just below `--- FAIL` shows the message.
- **Fix:** Reproduce one test: `go test ./pkg/... -run '^TestName$' -count=1 -v`.
- **Verify:** `go test ./... -count=1`

### GO-05 · Data race detected
- **Signature:** `WARNING: DATA RACE` · `race detected during execution of test` · `testing\.go:\d+: race detected`
- **Category:** test
- **Cause:** CI runs `go test -race`. Unsynchronised access to shared memory. It can look flaky because races depend on scheduling.
- **Fix:** Read both stacks ("Write at" / "Previous read at") and guard the shared state (mutex, channel, `atomic`), or stop sharing it.
- **Verify:** `go test -race -count=10 ./pkg/...`

### GO-06 · Test timeout / panic
- **Signature:** `panic: test timed out after \S+` · `^panic: ` · `goroutine \d+ \[running\]:`
- **Category:** test
- **Cause:** A deadlock or a blocking call without a timeout (network, channel), or a nil dereference that panics.
- **Fix:** For timeouts, read the goroutine dump for stuck goroutines (`chan receive`, `select`, `IO wait`) and add contexts with deadlines. For panics, the first frame in your code is the bug.
- **Verify:** `go test ./... -timeout 5m -count=1`

### GO-07 · golangci-lint failures
- **Signature:** `^\S+\.go:\d+:\d+: .+ \((?:errcheck|govet|staticcheck|unused|gosimple|ineffassign|revive|gofmt|goimports)\)` · `level=error msg="Running error:`
- **Category:** lint
- **Cause:** Lint findings, or golangci-lint built with an older Go than the module (typecheck errors) when its version isn't pinned.
- **Fix:** Pin `version:` in `golangci/golangci-lint-action` to a release built with your Go version, and fix findings (`golangci-lint run --fix`).
- **Verify:** `golangci-lint run ./...`

### GO-08 · gofmt / goimports diff
- **Signature:** `^diff -u \S+\.go\.orig` · `File is not .(?:gofmt|goimports|gofumpt).-ed`
- **Category:** lint
- **Cause:** Unformatted files.
- **Fix:** `gofmt -w .` (or `goimports -w .`) and commit.
- **Verify:** `test -z "$(gofmt -l .)"`

### GO-09 · Private modules can't be fetched
- **Signature:** `fatal: could not read Username for 'https://github\.com'` · `reading https://proxy\.golang\.org/.+: 404 Not Found` · `410 Gone`
- **Category:** config
- **Cause:** Private modules go through the public proxy, or git has no credentials in CI.
- **Fix:** `GOPRIVATE=github.com/your-org/*` and `git config --global url."https://x-access-token:${TOKEN}@github.com/".insteadOf "https://github.com/"` using a token secret.
- **Verify:** `go mod download`

### GO-10 · CGO / C toolchain issues
- **Signature:** `cgo: C compiler "\S+" not found` · `undefined reference to .\w+.` · `gcc: error: unrecognized command-line option`
- **Category:** build
- **Cause:** Packages need CGO (sqlite3, some crypto) and the runner, container or cross-compile target lacks a C toolchain. `CGO_ENABLED=0` builds then miss those symbols.
- **Fix:** Install `build-essential` (or `musl-tools` for static builds), set `CGO_ENABLED=1` where needed, or switch to pure-Go alternatives (e.g. `modernc.org/sqlite`).
- **Verify:** `CGO_ENABLED=1 go build ./...`

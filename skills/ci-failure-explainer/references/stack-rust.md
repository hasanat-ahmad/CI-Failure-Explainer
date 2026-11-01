# Rust CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`.

Quick orientation: check `rust-toolchain.toml` vs the toolchain action, `Cargo.lock` presence, the `rust-version` (MSRV) in `Cargo.toml`, and whether CI runs `clippy -D warnings`.

### RUST-01 · Compile error
- **Signature:** `^error\[E\d{4}\]: ` · `error: could not compile .\S+.` · `^\s+--> \S+\.rs:\d+:\d+`
- **Category:** build
- **Cause:** A real compile error, often from a dependency update (semver-minor breakage) when `Cargo.lock` isn't committed, or a feature combination CI builds and you don't.
- **Fix:** `rustc --explain EXXXX` and go to the `-->` location. Commit `Cargo.lock` for binaries. Test feature combinations with `cargo hack --each-feature check`.
- **Verify:** `cargo build --locked --all-targets`

### RUST-02 · Clippy warnings denied
- **Signature:** `= note: .-D clippy::\S+. (?:implied by|on by default)` · `-D warnings. implied by .-D warnings.` · `= help: for further information visit https://rust-lang\.github\.io/rust-clippy`
- **Category:** lint
- **Cause:** CI runs `cargo clippy -- -D warnings`, and a new stable Rust adds lints on the same code when the toolchain floats.
- **Fix:** `cargo clippy --fix --allow-dirty` plus manual fixes. Pin the toolchain in `rust-toolchain.toml` so new lints arrive deliberately.
- **Verify:** `cargo clippy --all-targets --all-features -- -D warnings`

### RUST-03 · rustfmt check
- **Signature:** `^Diff in \S+\.rs at line \d+:` · `rustfmt .* --check`
- **Category:** lint
- **Cause:** Unformatted code, or a different rustfmt version or edition.
- **Fix:** `cargo fmt --all` and commit.
- **Verify:** `cargo fmt --all -- --check`

### RUST-04 · `Cargo.lock` needs updating with `--locked`
- **Signature:** `the lock file \S+ needs to be updated but --locked was passed` · `cannot update the lock file \S+ because --(?:locked|frozen) was passed`
- **Category:** dependency
- **Cause:** `Cargo.toml` changed without updating `Cargo.lock`.
- **Fix:** `cargo update -p <crate>` (or `cargo generate-lockfile`) and commit.
- **Verify:** `cargo build --locked`

### RUST-05 · MSRV / toolchain too old
- **Signature:** `package .\S+. cannot be built because it requires rustc [\d.]+ or newer` · `requires rustc [\d.]+ or newer` · `feature .\S+. is required`
- **Category:** config
- **Cause:** A dependency raised its MSRV above the CI (or declared `rust-version`) toolchain, or edition-2024 code meets an old compiler.
- **Fix:** Bump the toolchain, or pin the dependency to a version that fits your MSRV (`cargo update -p crate --precise x.y.z`). Consider the MSRV-aware resolver (`resolver = "3"`).
- **Verify:** `cargo +<msrv> check --locked`

### RUST-06 · Test failure
- **Signature:** `^test \S+ \.\.\. FAILED$` · `^---- \S+ stdout ----$` · `thread '\S+' panicked at` · `test result: FAILED\.`
- **Category:** test
- **Cause:** An assertion failed or a test panicked. The `---- name stdout ----` block holds the message.
- **Fix:** `cargo test <name> -- --exact --nocapture` locally.
- **Verify:** `cargo test --locked`

### RUST-07 · Linker / native dependency missing
- **Signature:** `linking with .cc. failed` · `error: linker .\S+. not found` · `Could not find (?:openssl|OpenSSL)` · `failed to run custom build command for .\S+.`
- **Category:** build
- **Cause:** `-sys` crates need system libraries (OpenSSL, pkg-config, protobuf) that the runner or container lacks, or a cross target has no linker.
- **Fix:** `sudo apt-get install -y pkg-config libssl-dev protobuf-compiler`, or use rustls / vendored features (`openssl = { features = ["vendored"] }`). Use `cross` for cross-compilation.
- **Verify:** `cargo build --locked`

### RUST-08 · Build too slow / runs out of resources
- **Signature:** `error: could not compile .+ \(signal: 9, SIGKILL` · `rustc .+ terminated by signal` · `LLVM ERROR: out of memory`
- **Category:** infrastructure
- **Cause:** Release builds with LTO or many codegen units exhaust runner memory. Missing caches make every build cold.
- **Fix:** Cap parallelism (`CARGO_BUILD_JOBS=2`), lower debug info in CI (`CARGO_PROFILE_DEV_DEBUG=0`), cache with `Swatinem/rust-cache`.
- **Verify:** `CARGO_BUILD_JOBS=2 cargo build --release`

### RUST-09 · Feature-gated code not compiled in CI
- **Signature:** `unresolved import .\S+.` · `use of undeclared (?:crate or module|type) .\S+.` · `cannot find (?:function|struct|macro) .\S+. in (?:this scope|crate)`
- **Category:** build
- **Cause:** Code depends on a feature enabled locally (via the IDE or `--all-features`) but not in CI's `cargo` invocation, or the reverse.
- **Fix:** Gate the code with `#[cfg(feature = "x")]` correctly, or enable the feature in CI. Check combinations with `cargo hack`.
- **Verify:** `cargo check --no-default-features && cargo check --all-features`

### RUST-10 · `cargo deny` / audit advisories
- **Signature:** `error\[vulnerability\]` · `error\[(?:banned|unlicensed|rejected)\]` · `Crate:\s+\S+\s+Version:\s+\S+\s+Title:`
- **Category:** dependency
- **Cause:** A new RustSec advisory or license policy violation, even without code changes.
- **Fix:** `cargo update -p <crate>` to a patched version. If no fix exists, document a temporary ignore in `deny.toml` with the advisory ID and a review date.
- **Verify:** `cargo deny check` or `cargo audit`

# Docker / container build CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`. A backtick inside a log line is written as `.`.

Quick orientation: with BuildKit, the failing instruction appears as `#N [stage k/n] RUN ...` followed by `#N ERROR:` and a final `ERROR: failed to solve:` line. Check the **build context** (`context:` in `docker/build-push-action`, or the `.` in `docker build .`) and `.dockerignore` before anything else.

### DOCKER-01 · `COPY` source not found in build context
- **Signature:** `failed to calculate checksum of ref .+ not found` · `failed to compute cache key` · `COPY failed: (?:file not found|stat|forbidden)`
- **Category:** build
- **Cause:** The path being copied isn't in the build context. Either it's excluded by `.dockerignore`, or it's a build output (`dist/`, `build/`, `target/`) that only exists locally, or the context directory is wrong.
- **Fix:** Build the artifact **inside** the Dockerfile (multi-stage: `RUN npm run build` in a builder stage, then `COPY --from=builder /app/dist ./dist`), or produce it in an earlier workflow step and keep the context right. Check `.dockerignore` with `docker build --no-cache --progress=plain .`.
- **Verify:** `docker build --progress=plain -t test .`

### DOCKER-02 · Wrong build context or Dockerfile path
- **Signature:** `failed to read dockerfile` · `unable to prepare context` · `forbidden path outside the build context` · `open Dockerfile: no such file or directory`
- **Category:** config
- **Cause:** `docker/build-push-action` defaults to the git context (repo root) and `Dockerfile` at the root. Monorepos need `context:` and `file:`. `COPY ../x` can never leave the context.
- **Fix:**
  ```yaml
  - uses: docker/build-push-action@v6
    with:
      context: ./services/api
      file: ./services/api/Dockerfile
  ```
- **Verify:** `docker build -f services/api/Dockerfile services/api`

### DOCKER-03 · Base image tag missing or changed
- **Signature:** `manifest for \S+ not found` · `manifest unknown` · `pull access denied for \S+, repository does not exist`
- **Category:** dependency
- **Cause:** The tag was removed or renamed (e.g. EOL `node:16-buster`), mistyped, or is private. A floating tag (`latest`, `3`) can also move and change behaviour.
- **Fix:** Use an existing, supported tag and pin it for reproducibility: `FROM node:20.15-bookworm-slim` (or `@sha256:` digest, kept fresh with Dependabot or Renovate).
- **Verify:** `docker pull <image:tag>`

### DOCKER-04 · Multi-arch mismatch (arm64 vs amd64)
- **Signature:** `no matching manifest for linux/(?:arm64|amd64)` · `exec format error` · `The requested image's platform \(.+\) does not match the detected host platform`
- **Category:** build
- **Cause:** An image or binary built on Apple Silicon (arm64) runs on amd64 runners, or the base image doesn't publish the target arch.
- **Fix:** Build for the target explicitly: `platforms: linux/amd64,linux/arm64` with `docker/setup-qemu-action` + `docker/setup-buildx-action`. Download arch-specific binaries using `TARGETARCH`.
- **Verify:** `docker buildx build --platform linux/amd64 .`

### DOCKER-05 · Registry authentication failure
- **Signature:** `denied: requested access to the resource is denied` · `unauthorized: authentication required` · `denied: permission_denied: write_package` · `Error response from daemon: Get "?https://\S+"?: unauthorized`
- **Category:** permissions
- **Cause:** No `docker/login-action` step, an expired token, secrets unavailable (fork PR), or for GHCR, a `GITHUB_TOKEN` without `packages: write`.
- **Fix:**
  ```yaml
  permissions: { contents: read, packages: write }
  steps:
    - uses: docker/login-action@v3
      with: { registry: ghcr.io, username: "${{ github.actor }}", password: "${{ secrets.GITHUB_TOKEN }}" }
  ```
  Don't push on `pull_request` events from forks (`push: ${{ github.event_name != 'pull_request' }}`).
- **Verify:** `echo $TOKEN | docker login ghcr.io -u USER --password-stdin`

### DOCKER-06 · apt-get fails inside the image
- **Signature:** `E: Unable to locate package \S+` · `E: Package '\S+' has no installation candidate` · `Release file for .+ is not valid yet` · `404\s+Not Found \[IP:`
- **Category:** dependency
- **Cause:** `apt-get install` ran without `apt-get update` in the same layer (a stale cached index), the package was renamed in the new Debian release, or the base image's distro is EOL.
- **Fix:** `RUN apt-get update && apt-get install -y --no-install-recommends <pkgs> && rm -rf /var/lib/apt/lists/*` in one `RUN`. Move to a supported base image.
- **Verify:** `docker build --no-cache .`

### DOCKER-07 · A `RUN` instruction failed
- **Signature:** `process "/bin/(?:ba)?sh -c .+" did not complete successfully: exit code: \d+` · `executor failed running \[`
- **Category:** build
- **Cause:** The command inside the image failed. The real error is the output just above, in the same `#N` block.
- **Fix:** Read the `#N` lines before the failure (they're in the digest window) and treat the error as that tool's failure (npm, pip, go...) using the matching stack reference. Reproduce with `docker build --progress=plain --target <stage> .`.
- **Verify:** `docker build --progress=plain .`

### DOCKER-08 · Cache backend unsupported or failing
- **Signature:** `[Cc]ache export (?:feature )?is (?:currently )?not supported for docker driver` · `failed to (?:import|export|parse) (?:cache|registry cache)` · `error writing layer blob`
- **Category:** config
- **Cause:** `cache-to: type=gha` needs a buildx builder using the `docker-container` driver, not the default `docker` driver. GHA cache service hiccups can also fail the export.
- **Fix:** Add `docker/setup-buildx-action@v3` before building and use `cache-from: type=gha` / `cache-to: type=gha,mode=max`. Cache export errors on their own can be made non-fatal with `ignore-error=true`.
- **Verify:** Rerun the job and check the `importing cache manifest` lines

### DOCKER-09 · Build timeouts from layer cache misses
- **Signature:** `has exceeded the maximum execution time of \d+ minutes` · `CANCELED\s*$` · `importing cache manifest from .+ (?:not found|error)`
- **Category:** infrastructure
- **Cause:** Every build starts cold because layers are invalidated early (copying the whole repo before installing dependencies) or no cache is configured, so heavy steps rerun every time.
- **Fix:** Order the Dockerfile from least to most frequently changed: copy the lockfile, install deps, then copy sources. Configure `cache-from` / `cache-to`. Use `--mount=type=cache` for package-manager caches.
- **Verify:** Compare `CACHED` lines across two consecutive builds

### DOCKER-10 · Compose service unhealthy or exits
- **Signature:** `dependency failed to start: container \S+ is unhealthy` · `container \S+ exited \(\d+\)` · `exited with code [1-9]\d*`
- **Category:** infrastructure
- **Cause:** A service (database, API) in `docker compose up` crashed or its healthcheck never passed, often because of missing env vars, port conflicts, or a too-short healthcheck `start_period` on slower runners.
- **Fix:** Print logs on failure: `docker compose logs --no-color` in an `if: failure()` step. Fix the service config. Use `depends_on: condition: service_healthy` with realistic intervals.
- **Verify:** `docker compose up --wait && docker compose ps`

### DOCKER-11 · Multi-platform build with the wrong builder
- **Signature:** `[Mm]ulti(?:ple)?[- ]platform(?:s)? (?:feature|build) is (?:currently )?not supported for docker driver` · `docker exporter does not currently support exporting manifest lists`
- **Category:** config
- **Cause:** `platforms:` lists more than one platform, but buildx uses the default `docker` driver.
- **Fix:** Add `docker/setup-qemu-action@v3` and `docker/setup-buildx-action@v3` before `docker/build-push-action`. You can't `load: true` a multi-platform image. Push it, or build one platform for tests.
- **Verify:** `docker buildx ls`

### DOCKER-12 · Dockerfile syntax error
- **Signature:** `dockerfile parse error` · `unknown instruction: \S+` · `failed to solve with frontend dockerfile\.v0`
- **Category:** build
- **Cause:** Typos, an unescaped line continuation, a heredoc without `# syntax=docker/dockerfile:1`, or CRLF line endings committed from Windows.
- **Fix:** Fix the reported line. Add `# syntax=docker/dockerfile:1` for newer features. Normalise line endings with `.gitattributes` (`Dockerfile text eol=lf`).
- **Verify:** `docker build --check .` (BuildKit build checks) or `hadolint Dockerfile`

### DOCKER-13 · Docker Hub pull rate limit
- **Signature:** `toomanyrequests: You have reached your pull rate limit` · `You have reached your unauthenticated pull rate limit`
- **Category:** infrastructure
- **Cause:** Anonymous pulls are rate-limited per IP, and shared runner IPs hit the limit.
- **Fix:** Log in to Docker Hub (`docker/login-action` with a read-only token) or mirror base images to GHCR or ECR. Rerun as a short-term fix.
- **Verify:** `docker pull <image>` after login

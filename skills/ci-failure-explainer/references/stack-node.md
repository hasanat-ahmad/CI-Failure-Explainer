# Node.js / TypeScript CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`. A backtick inside a log line is written as `.`.

Quick orientation: compare the CI Node version (`actions/setup-node` → `node-version`) with `.nvmrc` / `engines`. Check whether the project uses npm, yarn, pnpm or bun (lockfile) and whether CI uses the same one. `CI=true` changes behaviour in several tools.

### NODE-01 · `npm ci`: lockfile out of sync with package.json
- **Signature:** `.npm ci. can only install packages when your package\.json and package-lock\.json` · `Missing: \S+ from lock file` · `Invalid: lock file's \S+ does not satisfy`
- **Category:** dependency
- **Cause:** `package.json` changed (a dependency was added or bumped) but `package-lock.json` wasn't regenerated and committed, or was made by a much older or newer npm.
- **Fix:** Run `npm install` with the same npm major version as CI, commit `package-lock.json`, and don't hand-edit it. Use `npm ci` in CI, never `npm install`.
- **Verify:** `rm -rf node_modules && npm ci`

### NODE-02 · Node version mismatch (`engines`, new syntax)
- **Signature:** `EBADENGINE` · `Unsupported engine` · `The engine "node" is incompatible with this module` · `SyntaxError: Unexpected token '(?:\?\?=|\?\.|\|\|=)'`
- **Category:** config
- **Cause:** CI runs a Node version outside a dependency's `engines` range, or one too old for syntax the code uses.
- **Fix:** Use one source of truth:
  ```yaml
  - uses: actions/setup-node@v4
    with:
      node-version-file: .nvmrc
      cache: npm
  ```
- **Verify:** `node --version && npm ci`

### NODE-03 · Peer dependency conflict (`ERESOLVE`)
- **Signature:** `ERESOLVE (?:unable to resolve|could not resolve)` · `Conflicting peer dependency` · `While resolving: \S+`
- **Category:** dependency
- **Cause:** npm 7+ enforces peer dependencies. A package needs a peer version (often `react`, `eslint`, `typescript`) incompatible with what's installed.
- **Fix:** Upgrade the package that has the outdated peer range, or align the peer version. `--legacy-peer-deps` only hides it. If you use it, put it in `.npmrc` so local and CI agree, and leave a TODO.
- **Verify:** `npm ci && npm ls <peer-package>`

### NODE-04 · ESM / CommonJS import errors
- **Signature:** `ERR_REQUIRE_ESM` · `Cannot use import statement outside a module` · `require\(\) of ES Module` · `ERR_MODULE_NOT_FOUND` · `SyntaxError: Unexpected token 'export'`
- **Category:** build
- **Cause:** A dependency went ESM-only (e.g. `chalk@5`, `node-fetch@3`, `nanoid@4`) while the code or the test runner loads it as CommonJS, or `"type": "module"` changed.
- **Fix:** Use `import()` / migrate to ESM, pin the last CJS major, or configure the runner (Jest: `transformIgnorePatterns` + babel/ts-jest ESM preset; or switch to Vitest). ESM needs file extensions in relative imports.
- **Verify:** `node -e "import('<pkg>').then(()=>console.log('ok'))" && npm test`

### NODE-05 · TypeScript errors that only show up in CI
- **Signature:** `error TS\d{4}:` · `Found \d+ errors?(?: in \d+ files?)?\.?$`
- **Category:** build
- **Cause:** The editor uses a different TS version or tsconfig than `tsc` in CI. Or `skipLibCheck` differs, generated types are missing (Prisma, GraphQL codegen), or a dependency bumped its typings.
- **Fix:** Run the CI command (`npx tsc --noEmit -p tsconfig.json`) locally with the lockfile's TypeScript. Add codegen steps (`npx prisma generate`) before type-checking in CI.
- **Verify:** `npx tsc --noEmit`

### NODE-06 · ESLint failure
- **Signature:** `✖ \d+ problems? \(\d+ errors?` · `^\s+\d+:\d+\s+error\s+.+\s+[\w/@-]+$`
- **Category:** lint
- **Cause:** Lint errors. Common surprise: CI uses `--max-warnings 0`, or a shared config or plugin got a new major via a loose range.
- **Fix:** `npx eslint . --fix`, fix the rest by hand. Pin ESLint plugins with the lockfile.
- **Verify:** `npx eslint . --max-warnings 0`

### NODE-07 · Prettier check fails
- **Signature:** `\[warn\] Code style issues found` · `Run Prettier with --write to fix` · `Code style issues found in \d+ files?`
- **Category:** lint
- **Cause:** Unformatted files, or a different Prettier version (3.x changed defaults such as `trailingComma: "all"`).
- **Fix:** `npx prettier --write .` using the lockfile's version, then commit.
- **Verify:** `npx prettier --check .`

### NODE-08 · Jest / Vitest assertion failure
- **Signature:** `^\s*●\s+\S.*›` · `expect\(received\)\.` · `AssertionError: expected .+ to (?:be|equal|deeply equal)` · `^\s*Tests:\s+\d+ failed`
- **Category:** test
- **Cause:** A test assertion failed. Look at `Expected` / `Received` and the changed files: usually a behaviour change, sometimes a stale snapshot or test data.
- **Fix:** Reproduce one test: `npx jest path/to/file -t "<test name>"` (Vitest: `npx vitest run path -t "<name>"`). Update snapshots (`-u`) only when the change is intended.
- **Verify:** `npm test -- --ci`

### NODE-09 · Jest doesn't exit (open handles)
- **Signature:** `Jest did not exit one second after the test run has completed` · `A worker process has failed to exit gracefully` · `--detectOpenHandles`
- **Category:** test
- **Cause:** A server, DB connection, timer or socket stays open after the tests finish, so the job hangs until timeout.
- **Fix:** Close resources in `afterAll` (`await server.close()`, `await prisma.$disconnect()`), use fake timers, and find the leak with `npx jest --detectOpenHandles`. `--forceExit` is a last resort.
- **Verify:** `npx jest --detectOpenHandles`

### NODE-10 · Test timeout too low for slower CI runners
- **Signature:** `Exceeded timeout of \d+ ?ms for a (?:test|hook)` · `Test timed out in \d+ ?ms` · `Hook timed out in \d+ ?ms`
- **Category:** test
- **Cause:** CI runners are slower than dev machines. Default limits (Jest 5 s, Vitest 5 s) are too tight for heavy setup, or a promise never resolves. If the test makes network calls, see FLAKY-01.
- **Fix:** Find what's slow (DB setup, real HTTP calls). Mock external calls, or raise the timeout for that test only (`test(name, fn, 20_000)`), not globally.
- **Verify:** `npx jest path/to/file --runInBand`

### NODE-11 · JavaScript heap out of memory
- **Signature:** `JavaScript heap out of memory` · `Reached heap limit Allocation failed` · `FATAL ERROR: (?:Ineffective mark-compacts|CALL_AND_RETRY_LAST)`
- **Category:** infrastructure
- **Cause:** Builds (webpack, Next.js, tsc on large repos, Jest with many workers) exceed Node's default heap.
- **Fix:** `NODE_OPTIONS=--max-old-space-size=4096` for the step, and limit parallelism (`jest --maxWorkers=2`). If the process is killed with exit 137 instead, the runner itself is out of memory (GEN-03).
- **Verify:** `NODE_OPTIONS=--max-old-space-size=4096 npm run build`

### NODE-12 · Build-time env vars missing (Next.js, Vite, env validation)
- **Signature:** `Missing required environment variable` · `Invalid environment variables` · `NEXT_PUBLIC_\w+ is (?:not defined|undefined)` · `import\.meta\.env\.\w+ is undefined`
- **Category:** config
- **Cause:** `NEXT_PUBLIC_*` / `VITE_*` values are inlined at build time. CI doesn't set them, or they come from secrets that fork PRs can't access. Env validation (t3-env, zod) fails the build.
- **Fix:** Set non-secret build values with `env:` in the workflow (or `vars.*`) and secrets with `secrets.*`. For PR builds without secrets, provide safe placeholders or `SKIP_ENV_VALIDATION=1`.
- **Verify:** `env | grep -E 'NEXT_PUBLIC|VITE_' ; npm run build`

### NODE-13 · Import path casing works on macOS, fails on Linux
- **Signature:** `Module not found: Error: Can't resolve '\.{1,2}/[^']*[A-Z][^']*'` · `Cannot find module '\.{1,2}/[^']+'` · `differs from already included file name .+ only in casing`
- **Category:** build
- **Cause:** macOS and Windows filesystems are case-insensitive, Linux runners are not. `import Button from './button'` resolves locally even when the file is `Button.tsx`.
- **Fix:** Match the import to the real file name. To rename a file's case in git: `git mv src/button.tsx src/Button.tsx`. Enable `forceConsistentCasingInFileNames` in tsconfig.
- **Verify:** `git ls-files | grep -i <name>` and `npm run build`

### NODE-14 · yarn / pnpm lockfile would change in CI
- **Signature:** `ERR_PNPM_OUTDATED_LOCKFILE` · `Your lockfile needs to be updated` · `The lockfile would have been modified by this install, which is explicitly forbidden` · `ERR_PNPM_NO_LOCKFILE`
- **Category:** dependency
- **Cause:** `--frozen-lockfile` / `--immutable` (the CI default) refuses to update an outdated lockfile. Also caused by a different yarn or pnpm version than the `packageManager` field.
- **Fix:** Run `pnpm install` / `yarn install` locally with the pinned version (`corepack enable`), commit the lockfile. Use `pnpm/action-setup` with the same version.
- **Verify:** `pnpm install --frozen-lockfile` (or `yarn install --immutable`)

### NODE-15 · Private registry authentication
- **Signature:** `npm (?:ERR!|error) code E401` · `npm (?:ERR!|error) code E404` · `ENEEDAUTH` · `Unable to authenticate, need: Basic`
- **Category:** config
- **Cause:** Private packages (GitHub Packages, npm org, Artifactory) need a token in CI. `.npmrc` references `${NODE_AUTH_TOKEN}`, but the env var isn't set, or it's a fork PR.
- **Fix:**
  ```yaml
  - uses: actions/setup-node@v4
    with: { node-version-file: .nvmrc, registry-url: https://npm.pkg.github.com }
  - run: npm ci
    env: { NODE_AUTH_TOKEN: "${{ secrets.NPM_TOKEN }}" }
  ```
  For GitHub Packages, grant `packages: read`.
- **Verify:** `npm whoami --registry <url>`

### NODE-16 · Playwright / Cypress browsers missing
- **Signature:** `Executable doesn't exist at .+ms-playwright` · `Please run the following command to download new browsers` · `Cypress failed to start` · `Cypress could not verify that this server is running`
- **Category:** config
- **Cause:** Browser binaries aren't installed (they're not in `node_modules`), the cache is stale after a version bump, or the app server never started.
- **Fix:** `npx playwright install --with-deps` after `npm ci`. For Cypress, use `cypress-io/github-action` with `start:` and `wait-on:`.
- **Verify:** `npx playwright test --project=chromium`

### NODE-17 · Native module build fails (node-gyp)
- **Signature:** `gyp ERR!` · `node-pre-gyp ERR!` · `prebuild-install (?:warn|WARN) install No prebuilt binaries found`
- **Category:** dependency
- **Cause:** No prebuilt binary exists for this Node version or OS (e.g. `sharp`, `bcrypt`, `canvas`), so npm compiles from source and lacks Python or build tools.
- **Fix:** Use a Node LTS that has prebuilds, upgrade the package, or install build deps (`sudo apt-get install -y build-essential python3`).
- **Verify:** `npm rebuild <pkg> && npm test`

### NODE-18 · `CI=true` turns warnings into errors (CRA and others)
- **Signature:** `Treating warnings as errors because process\.env\.CI = true` · `Failed to compile\.`
- **Category:** lint
- **Cause:** Create React App (and some other tools) fail the build on lint warnings when `CI=true`, which GitHub Actions always sets.
- **Fix:** Fix the warnings (preferred). Don't set `CI=false` globally, because it changes other tools too.
- **Verify:** `CI=true npm run build`

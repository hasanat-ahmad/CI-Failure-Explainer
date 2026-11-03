# Ruby / Rails CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`.

Quick orientation: compare `ruby/setup-ruby` `ruby-version` with `.ruby-version` / the `ruby` line in `Gemfile`, check the `BUNDLED WITH` and `PLATFORMS` sections of `Gemfile.lock`, and remember CI usually installs in frozen (deployment) mode, so the lockfile must already be right.

### RUBY-01 · `Gemfile.lock` out of date (frozen / deployment mode)
- **Signature:** `You are trying to install in (?:deployment|frozen) mode after changing your Gemfile` · `The gemspecs for path gems changed` · `The list of sources changed`
- **Category:** dependency
- **Cause:** `Gemfile` changed but `Gemfile.lock` wasn't regenerated and committed. `ruby/setup-ruby` with `bundler-cache: true` sets `BUNDLE_DEPLOYMENT`, which refuses to modify the lockfile.
- **Fix:** Run `bundle install` locally with the same Bundler as `BUNDLED WITH` (`gem install bundler -v <version>`) and commit `Gemfile.lock`.
- **Verify:** `bundle config set --local frozen true && bundle install`

### RUBY-02 · `Gemfile.lock` lacks the CI platform (`x86_64-linux`)
- **Signature:** `Your bundle only supports platforms \[[^\]]*\] but your local platform is` · `Could not find gems matching '[^']+' valid for all resolution platforms` · `Unable to find a spec satisfying \S+ in the set\. Perhaps the lockfile is corrupted`
- **Category:** dependency
- **Cause:** The lockfile was generated on a Mac (`arm64-darwin`) and only lists that platform. Linux runners need `x86_64-linux` (or `aarch64-linux`) entries for gems with precompiled variants such as `nokogiri`, `ffi` and `grpc`.
- **Fix:** `bundle lock --add-platform x86_64-linux` (and `aarch64-linux` for ARM runners), then commit `Gemfile.lock`.
- **Verify:** `bundle lock --add-platform x86_64-linux && git diff --exit-code Gemfile.lock`

### RUBY-03 · Ruby version mismatch
- **Signature:** `Your Ruby version is [\d.]+, but your Gemfile specified [~>=<\d. ]+` · `requires Ruby version [~>=<\d., ]+\. The current ruby version is` · `ruby-version input not given and no \.ruby-version`
- **Category:** config
- **Cause:** The workflow hard-codes a `ruby-version` that differs from `.ruby-version` or the `ruby` directive in `Gemfile`, or a gem dropped support for the CI Ruby.
- **Fix:** Let `ruby/setup-ruby@v1` read the project's pin (omit `ruby-version`, or set `ruby-version: .ruby-version`). Keep one source of truth for the version.
- **Verify:** `ruby -v && bundle exec ruby -e 'puts RUBY_VERSION'`

### RUBY-04 · Gem missing at runtime (`Bundler::GemNotFound`, `LoadError`)
- **Signature:** `Bundler::GemNotFound` · `Could not find [\w.-]+-\d[\w.]* in (?:locally installed gems|any of the sources)` · `cannot load such file -- \S+ \(LoadError\)`
- **Category:** dependency
- **Cause:** The gem isn't in `Gemfile` (works locally because it's installed globally), it sits in a group excluded by `BUNDLE_WITHOUT`, or a `vendor/bundle` cache from another Ruby version was restored.
- **Fix:** Add the gem to the right `Gemfile` group and commit the lockfile. Use `bundler-cache: true` in `ruby/setup-ruby`, which keys the cache on Ruby version and lockfile, instead of a hand-rolled `actions/cache`.
- **Verify:** `bundle install && bundle exec ruby -e 'require "<gem>"'`

### RUBY-05 · Native extension fails to build
- **Signature:** `ERROR: +Failed to build gem native extension` · `An error occurred while installing [\w.-]+ \([\d.]+\), and Bundler cannot\s+continue` · `Gem files will remain installed in \S+ for inspection`
- **Category:** build
- **Cause:** System headers are missing on the runner or in the image (`libpq-dev` for `pg`, `default-libmysqlclient-dev` for `mysql2`, `libvips` / `libyaml-dev` for `psych`), or an old gem version has no prebuilt binary for the new Ruby.
- **Fix:** Install the library before `bundle install` (`sudo apt-get install -y libpq-dev`), or upgrade the gem to a release that ships precompiled platform gems.
- **Verify:** `docker run --rm -v "$PWD":/app -w /app ruby:<version> bundle install`

### RUBY-06 · RSpec example failures
- **Signature:** `^\s*Failure/Error: ` · `^rspec \./\S+_spec\.rb:\d+` · `^\s*\d+ examples?, [1-9]\d* failures?`
- **Category:** test
- **Cause:** A real assertion failure. The lines under `Failure/Error:` show expected vs got. RSpec randomises order (`Randomized with seed N`), so a failure that only appears with some seeds is order dependence (see FLAKY-03).
- **Fix:** Rerun the exact example from the `rspec ./spec/...:LINE` list. If it passes alone, rerun the whole suite with the seed printed in the log.
- **Verify:** `bundle exec rspec ./spec/<file>_spec.rb:<line> && bundle exec rspec --seed <seed>`

### RUBY-07 · Minitest / `rails test` failures
- **Signature:** `^\s*\d+ runs, \d+ assertions, (?:[1-9]\d* failures|\d+ failures, [1-9]\d* errors)` · `^\w+#test_\w+ \[\S+:\d+\]:?$` · `^rails test \S+_test\.rb:\d+`
- **Category:** test
- **Cause:** A failing assertion or an exception in a test. Minitest prints `Failure:` / `Error:` followed by `TestClass#test_name [file:line]` and the message.
- **Fix:** Run the single test from the `rails test path:line` hint. Parallel tests (`parallelize(workers:)`) can hide shared-state bugs; retry with `PARALLEL_WORKERS=1`.
- **Verify:** `bin/rails test <file>_test.rb:<line>`

### RUBY-08 · RuboCop offenses
- **Signature:** `\d+ files? inspected, [1-9]\d* offenses? detected` · `^\S+\.rb:\d+:\d+: [CWEF]: (?:\[Correctable\] )?\w+/\w+:`
- **Category:** lint
- **Cause:** New offenses, or RuboCop / plugin versions differ between local and CI (RuboCop not locked in `Gemfile.lock`, or `NewCops: enable` picking up cops added in a new release).
- **Fix:** `bundle exec rubocop -a` for safe autocorrections and fix the rest by hand. Keep `rubocop` and its plugins in the `Gemfile` so CI and local run the same version.
- **Verify:** `bundle exec rubocop`

### RUBY-09 · Database unavailable or schema not loaded (Rails)
- **Signature:** `ActiveRecord::(?:NoDatabaseError|ConnectionNotEstablished|PendingMigrationError)` · `PG::ConnectionBad` · `Mysql2::Error::ConnectionError` · `connection to server at "[^"]+"[^,]*, port \d+ failed`
- **Category:** config
- **Cause:** The `services:` database has no health check (tests start before it accepts connections), `DATABASE_URL` points to `localhost` from inside a container job (use the service name), or the schema was never loaded.
- **Fix:** Add `options: --health-cmd pg_isready --health-interval 5s --health-retries 10` to the service, set `DATABASE_URL`, and run `bin/rails db:prepare` before the tests.
- **Verify:** `RAILS_ENV=test bin/rails db:prepare && bin/rails test`

### RUBY-10 · Rails credentials or `secret_key_base` missing
- **Signature:** `ActiveSupport::MessageEncryptor::InvalidMessage` · `Missing (?:encryption key to decrypt file with|.?secret_key_base.?)` · `key must be 16 bytes` · `ActiveSupport::EncryptedFile::MissingKeyError`
- **Category:** config
- **Cause:** `config/master.key` is git-ignored (correctly), so CI can't decrypt credentials. It also happens on fork PRs, where `RAILS_MASTER_KEY` is empty (see GEN-01).
- **Fix:** Provide `RAILS_MASTER_KEY: ${{ secrets.RAILS_MASTER_KEY }}` only where needed, keep the test environment free of credentials, and use `SECRET_KEY_BASE_DUMMY=1` for `assets:precompile` (Rails 7.1+).
- **Verify:** `RAILS_ENV=production SECRET_KEY_BASE_DUMMY=1 bin/rails assets:precompile`

### RUBY-11 · Asset build fails (`jsbundling` / `cssbundling` / Sprockets)
- **Signature:** `(?:js|css)bundling-rails: Command (?:build|install) failed` · `Sprockets::FileNotFound` · `ExecJS::RuntimeUnavailable` · `Webpacker can't find \S+ in \S+manifest\.json`
- **Category:** build
- **Cause:** The asset step needs Node and JavaScript dependencies that the Ruby-only job never installed, or an asset referenced in a view is missing from the build output.
- **Fix:** Add `actions/setup-node` and `yarn install --frozen-lockfile` (or `npm ci`) before `bin/rails assets:precompile`. For `FileNotFound`, check the asset path and the `app/assets/config/manifest.js` links.
- **Verify:** `RAILS_ENV=test bin/rails assets:precompile`

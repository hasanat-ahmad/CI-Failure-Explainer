# PHP CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`.

Quick orientation: compare `shivammathur/setup-php` `php-version` and `extensions` with `require.php` / `ext-*` in `composer.json` and `config.platform.php`. Composer's "Your lock file does not contain a compatible set of packages" is only an umbrella message: the real reason is in the `Problem 1 - …` lines under it.

### PHP-01 · `composer.lock` out of date with `composer.json`
- **Signature:** `The lock file is not up to date with the latest changes in composer\.json` · `Required package "[^"]+" is not present in the lock file` · `composer\.lock is not up to date with the latest changes in composer\.json`
- **Category:** dependency
- **Cause:** `composer.json` was edited (or badly merged) without running `composer update`, so the committed lock doesn't contain the new requirement.
- **Fix:** `composer update <vendor/package>` for just the changed package (not a full `composer update`) and commit `composer.lock`. Add `composer validate --strict` to CI to catch this early.
- **Verify:** `composer validate --strict && composer install --no-interaction`

### PHP-02 · PHP version or extension doesn't match platform requirements
- **Signature:** `requires? php \S+ (?:but your php version \([\d.]+\)|-> your php version \([\d.]+\)) does not satisfy that requirement` · `requires? ext-[\w-]+ \S+ -> it is missing from your system` · `Composer detected issues in your platform: Your Composer dependencies require a PHP version`
- **Category:** config
- **Cause:** The workflow installs a different PHP than the lockfile was resolved for, or a required extension (`intl`, `gd`, `pdo_pgsql`, `redis`) isn't enabled on the runner.
- **Fix:** Match CI to the project: `shivammathur/setup-php@v2` with `php-version` from `composer.json` and `extensions: intl, pdo_pgsql`. Set `config.platform.php` in `composer.json` so the lock always resolves for the production PHP.
- **Verify:** `composer check-platform-reqs`

### PHP-03 · Composer dependency conflict
- **Signature:** `Your requirements could not be resolved to an installable set of packages` · `Conclusion: don't install \S+` · `^\s*- \S+ \S+ conflicts with \S+`
- **Category:** dependency
- **Cause:** Two constraints can't both be met, often after bumping a framework major (`laravel/framework`, `symfony/*`) while a plugin still caps it.
- **Fix:** Ask Composer why: `composer why-not vendor/package <version>`. Upgrade or replace the blocking package, or keep the old major until it supports the new one.
- **Verify:** `composer update vendor/package --with-all-dependencies --dry-run`

### PHP-04 · Private repository or Packagist authentication
- **Signature:** `Could not authenticate against \S+` · `The "https?://[^"]+" file could not be downloaded \(HTTP/[\d.]+ 40[134]` · `Failed to clone \S+ via https, ssh protocols`
- **Category:** permissions
- **Cause:** A private VCS repository or a private Packagist / Satis needs credentials that CI doesn't have. It also happens on fork PRs, where secrets are empty (see GEN-01).
- **Fix:** Provide `COMPOSER_AUTH` from a secret, e.g. `COMPOSER_AUTH: '{"github-oauth": {"github.com": "${{ secrets.COMPOSER_TOKEN }}"}}'`, with a token that can read every private package repo.
- **Verify:** `composer install --no-interaction --prefer-dist`

### PHP-05 · PHPUnit / Pest test failures
- **Signature:** `^Tests: \d+, Assertions: \d+, (?:Errors: \d+, )?(?:Failures|Errors): [1-9]` · `Failed asserting that` · `^There (?:was|were) \d+ (?:failure|error)s?:$` · `^\s*FAILED\s+Tests\\\S+`
- **Category:** test
- **Cause:** An assertion failed. PHPUnit lists each failure as `1) Tests\Unit\CartTest::testVat` followed by the message and `file.php:line`.
- **Fix:** Run one test: `vendor/bin/phpunit --filter testVat` (or `vendor/bin/pest --filter "applies vat"`). If it only fails in CI, check the time zone (`date.timezone`), locale and test order (`--order-by=random --random-order-seed=<seed>`).
- **Verify:** `vendor/bin/phpunit --filter <test>`

### PHP-06 · PHPStan / Psalm errors
- **Signature:** `\[ERROR\] Found [1-9]\d* errors?` · `^ERROR: \w+ - \S+\.php:\d+:\d+ - ` · `Ignored error pattern .+ was not matched in reported errors`
- **Category:** lint
- **Cause:** New code has type errors, PHPStan / Psalm versions differ between local and CI (not locked), or the baseline is stale: a fixed error leaves an "ignored error pattern was not matched" failure.
- **Fix:** Fix the errors. For a stale baseline, regenerate it on purpose (`vendor/bin/phpstan analyse --generate-baseline`) and review the diff. Keep the analyser in `require-dev` so CI and local use the locked version.
- **Verify:** `vendor/bin/phpstan analyse --no-progress`

### PHP-07 · Coding standard check (PHP-CS-Fixer / PHP_CodeSniffer / Pint)
- **Signature:** `Found \d+ of \d+ files? that can be fixed` · `FOUND \d+ ERRORS? (?:AND \d+ WARNINGS? )?AFFECTING \d+ LINES?` · `^\s*FAIL\s+\.+\s+\d+ files?, \d+ style issues?`
- **Category:** lint
- **Cause:** Files don't match the project's style rules. CI runs the fixer in check mode (`--dry-run`, `phpcs`, `pint --test`).
- **Fix:** Run the fixer for real and commit: `vendor/bin/php-cs-fixer fix`, `vendor/bin/phpcbf`, or `vendor/bin/pint`.
- **Verify:** `vendor/bin/php-cs-fixer fix --dry-run --diff` (or `phpcs` / `pint --test`)

### PHP-08 · Laravel `APP_KEY` / `.env` missing in CI
- **Signature:** `No application encryption key has been specified` · `MissingAppKeyException` · `Failed to open stream: No such file or directory in \S*\.env`
- **Category:** config
- **Cause:** `.env` is git-ignored (correctly), so the CI checkout has no `APP_KEY` and no local settings.
- **Fix:** Before the tests: `cp .env.example .env && php artisan key:generate`, or set `APP_KEY` in `phpunit.xml` `<env>` for the test environment.
- **Verify:** `php artisan key:generate --show && php artisan test`

### PHP-09 · Database connection fails in tests
- **Signature:** `SQLSTATE\[HY000\] \[2002\] Connection refused` · `SQLSTATE\[08006\] \[7\] connection to server` · `SQLSTATE\[HY000\] \[1045\] Access denied for user` · `could not find driver`
- **Category:** config
- **Cause:** The `services:` database isn't ready yet (no health check), `DB_HOST=localhost` makes MySQL use a Unix socket that doesn't exist on the runner, credentials differ from the service's env, or the PDO extension isn't installed ("could not find driver").
- **Fix:** Add a health check to the service, use `DB_HOST=127.0.0.1` with the mapped port, match `DB_USERNAME` / `DB_PASSWORD` to the service env, and add `pdo_mysql` / `pdo_pgsql` to setup-php `extensions`.
- **Verify:** `php artisan migrate --env=testing` (or `bin/console doctrine:database:create --env=test`)

### PHP-10 · PHP memory limit exhausted
- **Signature:** `Allowed memory size of \d+ bytes exhausted` · `PHP Fatal error: +Allowed memory size`
- **Category:** infrastructure
- **Cause:** Static analysis, coverage or Composer resolution exceeds `memory_limit` in the runner's `php.ini`.
- **Fix:** Raise the limit for that command only: `vendor/bin/phpstan analyse --memory-limit=1G`, `php -d memory_limit=-1 vendor/bin/phpunit`, or `COMPOSER_MEMORY_LIMIT=-1 composer install`. Or set `ini-values: memory_limit=1G` in setup-php.
- **Verify:** `php -r 'echo ini_get("memory_limit"), PHP_EOL;'` and rerun the command

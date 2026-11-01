# JVM (Java / Kotlin, Gradle / Maven, native Android) CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`. Flutter projects: see `stack-flutter.md` first. It covers the Android/Gradle issues Flutter apps hit.

Quick orientation: the line after Gradle's `* What went wrong:` is the root cause. Compare `actions/setup-java` `java-version` with the toolchain in `build.gradle(.kts)` / `pom.xml`, and the Gradle wrapper version with the AGP/Kotlin plugin versions.

### JVM-01 · JDK version mismatch
- **Signature:** `Unsupported class file major version \d+` · `has been compiled by a more recent version of the Java Runtime \(class file version [\d.]+\)` · `invalid (?:target|source) release: \d+` · `error: release version \d+ not supported`
- **Category:** config
- **Cause:** CI's JDK differs from what the build targets (61=17, 65=21). Maven's `maven.compiler.release` or a Gradle toolchain asks for a JDK that isn't installed.
- **Fix:** `actions/setup-java@v4` with `distribution: temurin` and the right `java-version`. With Gradle toolchains, also allow auto-provisioning or install that JDK.
- **Verify:** `java -version && ./gradlew --version` (or `mvn -v`)

### JVM-02 · Dependency resolution failure
- **Signature:** `Could not resolve all (?:files|dependencies) for configuration` · `Could not find \S+:\S+:\S+\.` · `Could not resolve \S+:\S+:\S+` · `Failed to collect dependencies at`
- **Category:** dependency
- **Cause:** The artifact doesn't exist in the configured repositories (typo, removed, private repo without credentials), or a repository was down.
- **Fix:** Check the coordinates on the repository, add the right `repositories {}` entry, and provide credentials from secrets for private repos. Use the Gradle or Maven cache to reduce outage impact.
- **Verify:** `./gradlew dependencies --configuration runtimeClasspath` (or `mvn dependency:tree`)

### JVM-03 · Java/Kotlin compilation error
- **Signature:** `^e: (?:file://)?\S+\.kt:\d+:\d+ ` · `^\S+\.java:\d+: error: ` · `error: cannot find symbol` · `Compilation failed; see the compiler error output for details` · `COMPILATION ERROR`
- **Category:** build
- **Cause:** Real compile errors, generated sources (annotation processors, protobuf, KSP) missing in CI, or a dependency API change.
- **Fix:** Go to the reported file:line. Make sure code-generation tasks run before compile in CI.
- **Verify:** `./gradlew compileJava compileKotlin` (or `mvn -q compile`)

### JVM-04 · Test failures (JUnit / Surefire)
- **Signature:** `Tests run: \d+, Failures: [1-9]\d*` · `There were failing tests\. See the report at` · `\d+ tests completed, \d+ failed` · `^> Task :\S*test\S* FAILED$`
- **Category:** test
- **Cause:** Assertion failures. The details are in the test report rather than the log.
- **Fix:** Upload `build/reports/tests/` or `target/surefire-reports/` as an artifact (`if: failure()`), or rerun with `./gradlew test --info` / `mvn -Dtest=Class#method test`.
- **Verify:** `./gradlew test --tests '<Class.method>'`

### JVM-05 · Gradle wrapper missing or not executable
- **Signature:** `\./gradlew: Permission denied` · `Could not find or load main class org\.gradle\.wrapper\.GradleWrapperMain` · `gradle-wrapper\.jar.* not found`
- **Category:** config
- **Cause:** `gradlew` lost its executable bit, or `gradle/wrapper/gradle-wrapper.jar` is git-ignored.
- **Fix:** `git update-index --chmod=+x gradlew` and commit `gradle/wrapper/gradle-wrapper.jar`. Validate it with `gradle/actions/wrapper-validation`.
- **Verify:** `./gradlew --version`

### JVM-06 · Gradle daemon / JVM out of memory
- **Signature:** `java\.lang\.OutOfMemoryError: (?:Java heap space|Metaspace|GC overhead limit exceeded)` · `Gradle build daemon disappeared unexpectedly` · `Expiring Daemon because JVM heap space is exhausted`
- **Category:** infrastructure
- **Cause:** The default heap is too small for the build (Kotlin, R8/D8, lint), or too many parallel workers for the runner's RAM.
- **Fix:** In `gradle.properties`: `org.gradle.jvmargs=-Xmx4g -XX:MaxMetaspaceSize=1g` and `kotlin.daemon.jvmargs=-Xmx2g`. Reduce `org.gradle.workers.max` in CI.
- **Verify:** `./gradlew assemble --no-daemon`

### JVM-07 · Android lint errors
- **Signature:** `Lint found \d+ errors?` · `Lint found errors in the project; aborting build` · `^\S+\.(?:xml|kt|java):\d+: Error: `
- **Category:** lint
- **Cause:** Android lint runs during `build` / `check` with `abortOnError true`. A new AGP adds checks.
- **Fix:** Fix the findings (the HTML report has details). Baseline existing issues with `lint { baseline = file("lint-baseline.xml") }` instead of disabling lint.
- **Verify:** `./gradlew lint`

### JVM-08 · Kotlin / Gradle plugin incompatibility
- **Signature:** `Module was compiled with an incompatible version of Kotlin` · `The binary version of its metadata is \S+, expected version is \S+` · `Plugin \[id: '\S+'.*\] was not found in any of the following sources`
- **Category:** build
- **Cause:** Kotlin, KSP, Compose compiler and AGP versions must match. A plugin ID is missing from `pluginManagement` repositories.
- **Fix:** Align versions (use a version catalog: `gradle/libs.versions.toml`) and add `gradlePluginPortal()` / `google()` to `pluginManagement.repositories`.
- **Verify:** `./gradlew help`

### JVM-09 · Maven enforcer / checkstyle / spotless violations
- **Signature:** `Rule \d+: org\.apache\.maven\.enforcer\.rules\.\S+ failed` · `You have \d+ Checkstyle violations?` · `The following files had format violations` · `Run 'gradlew spotlessApply' to fix`
- **Category:** lint
- **Cause:** Policy or format checks fail: banned dependencies, the wrong Maven/Java version, style rules, unformatted code.
- **Fix:** Run the fixer (`./gradlew spotlessApply`, `mvn spotless:apply`) or address the named enforcer rule.
- **Verify:** `./gradlew spotlessCheck` or `mvn verify -DskipTests`

### JVM-10 · Signing config / keystore missing in CI
- **Signature:** `Keystore file '\S+' not found for signing config` · `KeystoreException` · `Failed to read key \S+ from store`
- **Category:** config
- **Cause:** Release builds reference a keystore that's git-ignored (correctly) and not restored from secrets.
- **Fix:** Decode the keystore from a base64 secret before building, and pass passwords via env vars read in `signingConfigs`. Build debug variants for PR checks.
- **Verify:** `./gradlew assembleRelease` with the env vars set

# Flutter / Dart CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) that `scripts/match_patterns.py` runs against the digest. A backtick inside a log line is written as `.`.

Quick orientation: Flutter CI usually breaks on **version drift** (Flutter/Dart SDK, Gradle, AGP, JDK, Kotlin, CocoaPods) rather than on code. Always compare the versions pinned in the workflow with `flutter --version`, `android/settings.gradle`, `android/gradle/wrapper/gradle-wrapper.properties` and `ios/Podfile`.

### FLUTTER-01 · Flutter/Dart SDK version drift between local and CI
- **Signature:** `The current Dart SDK version is \S+` · `requires SDK version [>=<^\d.]+` · `Because \S+ requires SDK version`
- **Category:** dependency
- **Cause:** CI installs a different Flutter (and so Dart) than the developer uses. Unpinned `subosito/flutter-action` follows the latest stable, while `pubspec.yaml` or a package needs a newer (or older) SDK.
- **Fix:** Pin the same version everywhere, ideally from one file:
  ```yaml
  - uses: subosito/flutter-action@v2
    with:
      flutter-version-file: pubspec.yaml   # reads environment.flutter
      channel: stable
      cache: true
  ```
  Or set `flutter-version: 3.24.5` explicitly and match it locally (`fvm use 3.24.5`).
- **Verify:** `flutter --version && flutter pub get`

### FLUTTER-02 · `pub get` version solving failed
- **Signature:** `version solving failed` · `^Because .+ (?:depends on|requires) .+` · `So, because \S+ depends on`
- **Category:** dependency
- **Cause:** Two packages (or a package and the SDK, e.g. `flutter_localizations` pinning `intl`) need incompatible versions. Often appears in CI first because the lockfile isn't committed or a different SDK resolves differently.
- **Fix:** Read the "Because ... " chain from bottom to top and relax the constraint the message names, e.g. `intl: ^0.19.0` → `intl: any` when the SDK pins it, or upgrade the conflicting package. Commit `pubspec.lock` for apps. Last resort for a transitive conflict: `dependency_overrides:` with a comment explaining why.
- **Verify:** `flutter pub get && flutter pub deps --style=compact | head`

### FLUTTER-03 · Android Gradle Plugin needs a newer JDK
- **Signature:** `Android Gradle plugin requires Java \d+ to run` · `Unsupported class file major version \d+` · `Dependency requires at least JVM runtime version \d+` · `has been compiled by a more recent version of the Java Runtime`
- **Category:** build
- **Cause:** AGP 8.x needs JDK 17+, while the runner (or `actions/setup-java`) provides JDK 11. The reverse (JDK 21 with old Gradle) produces "Unsupported class file major version 65".
- **Fix:**
  ```yaml
  - uses: actions/setup-java@v4
    with:
      distribution: temurin
      java-version: "17"
  ```
  Major versions: 55=Java 11, 61=Java 17, 65=Java 21. Keep the JDK, Gradle wrapper and AGP within the [compatibility table](https://developer.android.com/build/releases/gradle-plugin#compatibility).
- **Verify:** `java -version && cd android && ./gradlew --version && flutter build apk --debug`

### FLUTTER-04 · Gradle wrapper incompatible with JDK or AGP
- **Signature:** `Minimum supported Gradle version is \S+` · `Your build is currently configured to use incompatible Java \S+ and Gradle \S+` · `Could not initialize class org\.codehaus\.groovy`
- **Category:** build
- **Cause:** `gradle-wrapper.properties` points to a Gradle release that doesn't support the JDK on the runner or the AGP version in `settings.gradle`.
- **Fix:** Update `distributionUrl` in `android/gradle/wrapper/gradle-wrapper.properties` (e.g. `gradle-8.7-all.zip`) and AGP in `android/settings.gradle` together, following the AGP ↔ Gradle table. Don't download a different Gradle in CI than the wrapper.
- **Verify:** `cd android && ./gradlew --version && ./gradlew assembleDebug`

### FLUTTER-05 · Kotlin version mismatch
- **Signature:** `Module was compiled with an incompatible version of Kotlin` · `The binary version of its metadata is \S+, expected version is \S+` · `Your project requires a newer version of the Kotlin Gradle plugin`
- **Category:** build
- **Cause:** A plugin was compiled with a newer Kotlin than the project's Kotlin Gradle plugin (`org.jetbrains.kotlin.android` in `settings.gradle`, or `ext.kotlin_version`).
- **Fix:** Raise the Kotlin plugin version, e.g. `id "org.jetbrains.kotlin.android" version "1.9.24" apply false`, then `flutter clean`.
- **Verify:** `flutter build apk --debug`

### FLUTTER-06 · `minSdkVersion` lower than a plugin requires
- **Signature:** `uses-sdk:minSdkVersion \d+ cannot be smaller than version \d+` · `minSdkVersion \d+ cannot be smaller`
- **Category:** build
- **Cause:** A newly added or upgraded plugin needs a higher Android API level than `android/app/build.gradle` declares.
- **Fix:** In `android/app/build.gradle`: `defaultConfig { minSdkVersion 23 }` (use the version the error names). Flutter's default `flutter.minSdkVersion` may be too low.
- **Verify:** `flutter build apk --debug`

### FLUTTER-07 · CocoaPods / iOS deployment target errors
- **Signature:** `CocoaPods could not find compatible versions for pod` · `required a higher minimum deployment target` · `\[!\] Automatically assigning platform .?iOS.? with version` · `CocoaPods's specs repository is too out-of-date`
- **Category:** dependency
- **Cause:** The Podfile platform (or the Xcode project's `IPHONEOS_DEPLOYMENT_TARGET`) is lower than a pod requires, or the macOS runner's CocoaPods spec repo is stale.
- **Fix:** In `ios/Podfile`: `platform :ios, '13.0'` (use the version the error names), then `cd ios && pod repo update && pod install`. Commit `Podfile.lock`. Pin the Xcode version on macOS runners with `maxim-lobanov/setup-xcode` if images changed.
- **Verify:** `cd ios && pod install && flutter build ios --no-codesign`

### FLUTTER-08 · Firebase config files missing in CI
- **Signature:** `File google-services\.json is missing` · `GoogleService-Info\.plist` · `No matching client found for package name`
- **Category:** config
- **Cause:** `google-services.json` / `GoogleService-Info.plist` are git-ignored (correctly) but CI never recreates them.
- **Fix:** Store each file base64-encoded as a secret and write it before building:
  ```yaml
  - run: echo "$GOOGLE_SERVICES_JSON" | base64 --decode > android/app/google-services.json
    env:
      GOOGLE_SERVICES_JSON: ${{ secrets.GOOGLE_SERVICES_JSON }}
  ```
  Secrets are empty on fork PRs, so use a dummy config for PR builds that don't need Firebase.
- **Verify:** `test -s android/app/google-services.json && flutter build apk --debug`

### FLUTTER-09 · `dart format` check fails
- **Signature:** `Changed \S+\.dart` · `dart format .*--set-exit-if-changed` · `Formatted \d+ files? \(\d+ changed\)`
- **Category:** lint
- **Cause:** Code wasn't formatted, or a different Dart version formats differently (Dart 3.7 switched to the "tall" style).
- **Fix:** Run `dart format .` with the same Dart version CI uses and commit the result. Pin the SDK (FLUTTER-01) so formatter output is stable.
- **Verify:** `dart format --output=none --set-exit-if-changed .`

### FLUTTER-10 · `flutter analyze` fails on infos/warnings (`--fatal-infos`)
- **Signature:** `^\s*(?:info|warning) • .+ • \S+\.dart:\d+:\d+ •` · `\d+ issues? found\.`
- **Category:** lint
- **Cause:** Locally `flutter analyze` exits 0 for infos, but CI runs `flutter analyze --fatal-infos` (or `--fatal-warnings`), so lints like `unnecessary_import` fail the build. A newer Flutter in CI can also add new lints.
- **Fix:** Fix each reported `file:line:col` (the lint name is the last field; `dart fix --apply` handles many). Run the exact CI command locally. Adjust `analysis_options.yaml` only if the team agrees a rule should change.
- **Verify:** `flutter analyze --fatal-infos`

### FLUTTER-11 · Dart compile or analyzer errors
- **Signature:** `^\s*error • .+ • \S+\.dart:\d+:\d+ •` · `\S+\.dart:\d+:\d+: Error: ` · `Error: The (?:argument|getter|method|setter) .+ (?:can't|isn't)`
- **Category:** build
- **Cause:** Real type or API errors, often from a package upgrade with breaking changes, or code generated by a different build_runner version.
- **Fix:** Go to the reported `file:line:col`. If it's in a dependency API, check that package's CHANGELOG and pin or migrate.
- **Verify:** `flutter analyze && flutter test`

### FLUTTER-12 · Golden test pixel differences across OSes
- **Signature:** `Pixel test failed` · `Golden "[^"]+": ` · `isolatedDiff\.png|maskedDiff\.png`
- **Category:** test
- **Cause:** Goldens rendered on macOS differ from Linux runners (font rendering, anti-aliasing). Real UI changes produce this too.
- **Fix:** Generate and compare goldens on the same OS as CI (`flutter test --update-goldens` in a Linux container or on the CI runner), use a tolerance comparator, or load test fonts (`golden_toolkit` / `loadAppFonts()`). Upload the `failures/` directory as an artifact to see the diff.
- **Verify:** `flutter test --tags golden`

### FLUTTER-13 · `build_runner` generated code out of date
- **Signature:** `_\$\w+(?:FromJson|ToJson)` · `Conflicting outputs were detected` · `Target of URI hasn't been generated: .+\.(?:g|freezed)\.dart` · `uri_has_not_been_generated`
- **Category:** build
- **Cause:** `*.g.dart` / `*.freezed.dart` files are git-ignored or stale, and CI doesn't run code generation.
- **Fix:** Add a step before analyze/test: `dart run build_runner build --delete-conflicting-outputs`. If generated files are committed, add a CI check that regenerating gives no diff.
- **Verify:** `dart run build_runner build --delete-conflicting-outputs && flutter analyze`

### FLUTTER-14 · Integration tests have no device or emulator
- **Signature:** `No supported devices (?:connected|found)` · `No devices found` · `Error waiting for a debug connection` · `Timed out waiting for the emulator`
- **Category:** infrastructure
- **Cause:** `flutter test integration_test` needs a device. Plain `ubuntu-latest` has none.
- **Fix:** Use `reactivecircus/android-emulator-runner@v2` (enable KVM on Linux), or run on Firebase Test Lab, or use `flutter test -d linux` / `-d chrome` for platform-agnostic tests. Give the emulator boot a generous timeout.
- **Verify:** `flutter devices && flutter test integration_test`

### FLUTTER-15 · Android SDK licenses or packages missing
- **Signature:** `You have not accepted the license agreements` · `Failed to install the following Android SDK packages` · `Android sdkmanager not found`
- **Category:** config
- **Cause:** A new compileSdk / build-tools version isn't preinstalled on the runner and licenses aren't accepted.
- **Fix:** `yes | flutter doctor --android-licenses` or `yes | sdkmanager --licenses` before building, or use `android-actions/setup-android@v3`.
- **Verify:** `flutter doctor -v`

### FLUTTER-16 · Widget/unit test failure
- **Signature:** `EXCEPTION CAUGHT BY FLUTTER TEST FRAMEWORK` · `Some tests failed\.` · `^\s*Expected: .+` · `Test failed\. See exception logs above\.` · `^\d+:\d+ \+\d+(?: ~\d+)?(?: -\d+)?: .+ \[E\]$`
- **Category:** test
- **Cause:** A real assertion failure. Check the diff for behaviour changes first, then for async problems (missing `await tester.pumpAndSettle()`), which are the common flaky case.
- **Fix:** Reproduce with `flutter test path/to/test.dart --plain-name "<test name>"`. Fix the code or the expectation, whichever the diff shows is wrong.
- **Verify:** `flutter test`

### FLUTTER-17 · iOS code signing in CI
- **Signature:** `No signing certificate "[^"]+" found` · `requires a provisioning profile` · `No profiles for '[^']+' were found`
- **Category:** config
- **Cause:** Release iOS builds need certificates and profiles that aren't on the runner.
- **Fix:** For CI checks, build with `flutter build ios --no-codesign`. For releases, install signing assets from secrets (fastlane match, or `apple-actions/import-codesign-certs`).
- **Verify:** `flutter build ios --no-codesign`

### FLUTTER-18 · Asset or font declared in pubspec.yaml is missing in CI
- **Signature:** `unable to locate asset entry in pubspec\.yaml` · `unable to find directory entry in pubspec\.yaml` · `Error: Failed to build asset bundle`
- **Category:** config
- **Cause:** `pubspec.yaml` lists an asset or font file that isn't in the checkout: it's git-ignored (licensed fonts, generated or downloaded files), a fetch step was skipped or failed, or the path's case differs on Linux.
- **Fix:** Commit the file if its licence allows. Otherwise make CI fetch it before `pub get` / `test`, and make the fetch fail loudly (`curl -fsSL`, `set -euo pipefail`). Check the exact path and case with `git ls-files app/assets`. For optional assets, list the directory (`assets/fonts/`) rather than individual files.
- **Verify:** `flutter test` (or `flutter build`) in a fresh clone after running the fetch step

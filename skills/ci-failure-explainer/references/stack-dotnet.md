# .NET / C# CI failures

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`.

Quick orientation: compare `actions/setup-dotnet` `dotnet-version` with `global.json` (`sdk.version` and `rollForward`) and the projects' `<TargetFramework>`. Remember CI usually builds `-c Release`, where `TreatWarningsAsErrors` and analyzers are often stricter than in local Debug builds. Linux runners have case-sensitive file systems.

### DOTNET-01 · NuGet package or feed not found
- **Signature:** `error NU1101: Unable to find package` · `error NU1102: Unable to find package \S+ with version` · `error NU1301: Unable to load the service index for source`
- **Category:** dependency
- **Cause:** A private feed isn't configured or authenticated in CI (`nuget.config` lists it, but no credentials), the version was never published, or the feed is down (NU1301).
- **Fix:** Authenticate the feed in the workflow: `actions/setup-dotnet` with `source-url:` plus `NUGET_AUTH_TOKEN`, or `dotnet nuget add source <url> --username ci --password ${{ secrets.NUGET_TOKEN }} --store-password-in-clear-text`. Check the exact version exists on the feed.
- **Verify:** `dotnet nuget locals all --clear && dotnet restore`

### DOTNET-02 · Package downgrade or version conflict
- **Signature:** `error NU1605: (?:Warning As Error: )?Detected package downgrade` · `error NU1107: Version conflict detected for` · `error NU1608: Detected package version outside of dependency constraint`
- **Category:** dependency
- **Cause:** Two projects or packages need different versions of the same dependency, and NuGet would pick a lower one. NU1605 is a warning promoted to an error by default.
- **Fix:** Reference the higher version directly in the project that restores the lower one, or move to Central Package Management (`Directory.Packages.props`) so every project uses one version.
- **Verify:** `dotnet restore && dotnet list package --include-transitive`

### DOTNET-03 · `packages.lock.json` out of date (locked restore)
- **Signature:** `error NU1004: ` · `The packages? lock file is inconsistent with the project dependencies` · `The package references have changed for \S+\. Lock file's package references`
- **Category:** dependency
- **Cause:** CI restores with `--locked-mode` (or `RestoreLockedMode=true`), and a package reference changed without regenerating the lock file.
- **Fix:** `dotnet restore --force-evaluate` locally and commit every changed `packages.lock.json`.
- **Verify:** `dotnet restore --locked-mode`

### DOTNET-04 · .NET SDK version not installed (`global.json`)
- **Signature:** `A compatible \.NET SDK was not found` · `Requested SDK version: \d+\.\d+\.\d+` · `error NETSDK1045: The current \.NET SDK does not support targeting \.NET \d` · `error NETSDK1141: Unable to resolve the \.NET SDK version as specified in the global\.json`
- **Category:** config
- **Cause:** `global.json` pins an SDK that the runner doesn't have, or a project targets a newer framework (`net9.0`) than the installed SDK supports. Runner images ship a few SDKs, which change with image updates.
- **Fix:** Install exactly what the repo asks for: `actions/setup-dotnet@v4` with `global-json-file: global.json`. Use `"rollForward": "latestFeature"` in `global.json` if patch-level drift is acceptable.
- **Verify:** `dotnet --version && dotnet --list-sdks`

### DOTNET-05 · C# compile error
- **Signature:** `: error CS(?!86)\d{4}: ` · `error CS0246: The type or namespace name '[^']+' could not be found` · `error CS0103: The name '[^']+' does not exist in the current context`
- **Category:** build
- **Cause:** A real compile error, often after a package API change or a missing `using` / package reference. On Linux, a `<Compile Include>` or `ProjectReference` path with the wrong case also produces "type or namespace not found".
- **Fix:** Go to the `File.cs(line,col)` location. If the type comes from a package, check its release notes and pin the version that has it.
- **Verify:** `dotnet build -c Release --no-restore`

### DOTNET-06 · Warnings treated as errors in CI (nullable, analyzers)
- **Signature:** `error CS86\d\d: .*\bnull` · `: error CA\d{4}: ` · `error CS1591: Missing XML comment for publicly visible type`
- **Category:** lint
- **Cause:** The CI build uses `-warnaserror`, `TreatWarningsAsErrors` only in `Release`, or `<AnalysisLevel>latest</AnalysisLevel>`, so a newer SDK on the runner brings new analyzer rules. Nullable warnings (`CS8602`, `CS8618`, …) then fail the build.
- **Fix:** Fix the warnings (null checks, `required` members, initialisers). Pin the SDK in `global.json` so analyzers only change when you choose. Suppress a rule in `.editorconfig` only on purpose.
- **Verify:** `dotnet build -c Release -warnaserror`

### DOTNET-07 · `dotnet test` failures
- **Signature:** `^\s*Failed \S+ \[[\d.]+ ?m?s\]` · `^Failed! +- Failed: +[1-9]\d*, Passed:` · `Assert\.\w+\(\) Failure` · `\[xUnit\.net [\d:.]+\]\s+\S+ \[FAIL\]`
- **Category:** test
- **Cause:** An assertion failed. The `Failed <FullyQualifiedName>` line is followed by the message and stack trace with `in File.cs:line N`.
- **Fix:** Run the single test with a filter and fix the code or the expectation, whichever is wrong. If it only fails in CI, check culture and time-zone assumptions (`InvariantCulture`, UTC) and test parallelism.
- **Verify:** `dotnet test --filter "FullyQualifiedName~<TestName>" -c Release`

### DOTNET-08 · Test host crashed or hung
- **Signature:** `The active test run was aborted\. Reason: Test host process crashed` · `Test host process exited with error` · `The test running when the crash occurred` · `Blame: The specified inactivity time of \S+ has elapsed`
- **Category:** test
- **Cause:** A stack overflow, an out-of-memory crash, an unhandled exception on a background thread, or a deadlock. Results stop with no assertion message.
- **Fix:** Rerun with crash and hang collectors. The blame output names the test that was running. Upload the `TestResults/` folder as an artifact to get the dump.
- **Verify:** `dotnet test --blame-crash --blame-hang-timeout 5m`

### DOTNET-09 · `dotnet format` verification fails
- **Signature:** `error (?:WHITESPACE|IMPORTS|FINALNEWLINE|CHARSET|ENDOFLINE): ` · `: error IDE\d{4}: ` · `Formatted code file '[^']+'\.`
- **Category:** lint
- **Cause:** CI runs `dotnet format --verify-no-changes` (or builds with `EnforceCodeStyleInBuild`) and some files don't match `.editorconfig`. CRLF vs LF differences are common when Windows developers commit without `.gitattributes`.
- **Fix:** Run `dotnet format` and commit the result. Add `* text=auto eol=lf` to `.gitattributes` if line endings are the problem (see GEN-11).
- **Verify:** `dotnet format --verify-no-changes`

### DOTNET-10 · Windows-only project or API on a Linux runner
- **Signature:** `error NETSDK1100: To build a project targeting Windows on this operating system` · `System\.PlatformNotSupportedException` · `: (?:warning|error) CA1416: This call site is reachable on all platforms`
- **Category:** config
- **Cause:** A WPF / WinForms / `net8.0-windows` project or a Windows-only API (registry, `System.Drawing` on non-Windows, DPAPI) runs on `ubuntu-latest`.
- **Fix:** Run that job on `windows-latest`, build Windows-only projects with `-p:EnableWindowsTargeting=true` when you only need to compile them, or guard calls with `OperatingSystem.IsWindows()`.
- **Verify:** `dotnet build -p:EnableWindowsTargeting=true`

### DOTNET-11 · MSBuild path or import not found (case sensitivity)
- **Signature:** `error MSB3030: Could not copy the file "[^"]+" because it was not found` · `error MSB4019: The imported project "[^"]+" was not found` · `error MSB1009: Project file does not exist` · `error CS0006: Metadata file '[^']+' could not be found`
- **Category:** build
- **Cause:** A path in a `.csproj` / `.sln` differs in case or uses `\` where Linux needs `/`, an imported `.props` / `.targets` file isn't committed, or a referenced project failed to build first (CS0006 is a follow-on error).
- **Fix:** Fix the path's case to match the file on disk, and use `/` or MSBuild properties (`$(MSBuildThisFileDirectory)`). For CS0006, fix the first project that failed. It's earlier in the log.
- **Verify:** `dotnet build -c Release` in a Linux container: `docker run --rm -v "$PWD":/src -w /src mcr.microsoft.com/dotnet/sdk:8.0 dotnet build`

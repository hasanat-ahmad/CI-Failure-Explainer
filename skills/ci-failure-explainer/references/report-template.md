# Report template

Use this exact structure for every diagnosis. Keep it short: the verdict should fit on one line, and the evidence should be quotes, not paraphrase. Never include secret values, even if you suspect one leaked (say "a credential appears in line N of the raw log; rotate it").

```markdown
## CI failure: <workflow> › <job> › <step>

**Verdict:** <one sentence root cause>
**Category:** <build | test | lint | dependency | config | permissions | infrastructure | flaky>   **Confidence:** high | medium | low
**Likely flaky:** yes | no | unsure
**Known pattern:** <ID · title, or "none">

### Evidence
- Log: `<key log line(s), from the digest>`
- Change: `<file:line from the diff that caused it, if any>`
- Context: <one line: history strip, same-SHA result, version drift, fork PR, etc.>

### Fix
<what to change, with a short diff or snippet>

### Verify
`<command to run locally>`

### If this isn't it
<second hypothesis + the quickest check to rule it in or out>
```

## Rules

- **Verdict:** name the cause, not the symptom. "Jest failed" is a symptom. "`applyDiscount` now rounds before applying the 10% rate (changed in `src/cart.ts:42`), so the order-total test gets 89.99 instead of 90" is a cause.
- **Confidence:** see `diagnostic-method.md` step 6. If confidence is low, the "If this isn't it" section is mandatory and must name a concrete check.
- **Fix:** smallest change that addresses the root cause. If you applied it, say so and show the diff. If not, end the report with: *"Want me to apply this fix?"*
- **Verify:** a command the user can run locally. If local verification is impossible (secrets, macOS-only), give the CI-side check instead and say why.
- **Flaky = yes** requires evidence (same SHA passed, local repeat runs failed intermittently, etc.).
- **Multiple independent failures** (e.g. lint and tests in separate jobs): one short section per failure, most blocking first.

## Example

```markdown
## CI failure: CI › build › Install dependencies

**Verdict:** `package-lock.json` wasn't updated when `left-pad@1.3.0` was added to `package.json`, so `npm ci` refuses to install.
**Category:** dependency   **Confidence:** high
**Likely flaky:** no
**Known pattern:** NODE-01 · `npm ci`: lockfile out of sync with package.json

### Evidence
- Log: `npm ERR! Missing: left-pad@1.3.0 from lock file`
- Change: `package.json:24` adds `"left-pad": "^1.3.0"`, and `package-lock.json` is unchanged in the diff
- Context: last green `a1b2c3d`, history ✗✓✓✓ (first failure after this commit)

### Fix
Run `npm install` (npm 10, as in CI) and commit the updated `package-lock.json`.

### Verify
`rm -rf node_modules && npm ci && npm test`

### If this isn't it
If the lockfile *was* committed, CI may use a different npm major: compare `npm -v` locally with the "Setup node" step output.
```

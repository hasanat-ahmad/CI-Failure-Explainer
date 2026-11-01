## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `make check` passes (validate + tests + eval ≥ 9/10)
- [ ] `ruff check .` passes
- [ ] Standard library only, Python 3.9-compatible
- [ ] New/changed pattern entries use the standard format, and their signatures were tested against a (synthetic) log line
- [ ] Redaction changes include a test proving no secret leaks
- [ ] No real secrets, company logs or proprietary output anywhere in the diff
- [ ] CHANGELOG.md updated for user-visible changes

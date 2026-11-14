## Summary

What changed and why.

## Tests run

- [ ] `python tools/run_tests_per_file.py` (one process per test file, like CI)
- [ ] `for t in tests/*.test.mjs; do node --test "$t"; done`
- [ ] Manual check in Hermes Desktop (describe it, if any)

Paste the final summary line from each suite.

## Checklist

- [ ] A test fails without this change.
- [ ] No new runtime dependency in the plugin.
- [ ] Desktop code stays inside the plugin SDK (no new DOM reach-ins, raw bridges or `localStorage`).
- [ ] Any new subprocess passes `env=child_env()` (the test suite checks this).
- [ ] `LIMITS.md` updated if the change narrows what REVamp can promise.
- [ ] `CHANGELOG.md` updated for a user-visible change.
- [ ] No personal data, paths, email addresses or secrets in the diff.

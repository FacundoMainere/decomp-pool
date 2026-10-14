# Progress

Target: GAME (REGION) `GAMECODE`, SHA1 `rom_sha1 from pool.toml`

The maintainer keeps this file; contributors don't edit it. The hourly "Expire claims"
workflow keeps the line below current (`python3 tools/pool.py progress --write`); leave the
markers in place.

<!-- progress:start -->
0 / 0 bytes solved (0.00%), 0 / 0 functions
<!-- progress:end -->

## Setup (docs/NEW_PROJECT.md)

- [ ] Repository created from the template, tools installed, tool tests pass
- [ ] Ghidra function list exported
- [ ] Code region end found
- [ ] `functions.csv` generated, `rom_sha1` set
- [ ] Boundaries fixed (fragments, fake starts, nested functions)
- [ ] Unsolvable rows removed (see Exceptions)
- [ ] Library aliases in `pool.toml`
- [ ] Three functions matched locally
- [ ] Repository configured and published
- [ ] Loop tested: claim, solve, reject, release, expire
- [ ] Fork PR tested by a contributor

## Exceptions (removed from functions.csv)

| Range | What | Reason |
|---|---|---|

## Log

One line per session or milestone: the date, the progress numbers, and anything
notable (boundary fixes, new aliases, a new compiler flavour).

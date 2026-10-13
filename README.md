# decomp-pool

A template for matching decompilations that anyone can contribute to, by hand or by pointing
their own AI agent at the repository. Contributors need no write access: they claim
functions, solve them and release them through pull requests that GitHub Actions validates
and merges automatically. Nobody spends tokens or time reviewing routine contributions.

- **Contributing:** see [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).
- **Starting a new game:** see [docs/NEW_PROJECT.md](docs/NEW_PROJECT.md).
- **Design:** [docs/specs/2026-10-13-decomp-pool-design.md](docs/specs/2026-10-13-decomp-pool-design.md).

## How it works

- `functions.csv` lists every function to decompile with its size and the SHA1 of its
  original bytes. The repository never contains the ROM.
- A claim is a file `pool/claims/ADDR` holding the claimer and a timestamp. One file per
  record means concurrent PRs never conflict.
- A solved function is `src/func_ADDR.c`. CI compiles it with agbcc, refuses inline asm,
  data, comments and stray directives, links it at its address and compares the SHA1.
- Claims expire after `claim_hours`; each expired claim is a strike, and too many strikes
  stop a user from claiming. All limits are in `pool.toml`.

## Disclaimer

Unofficial and not affiliated with any rights holder. The tooling and docs are MIT-licensed
(see LICENSE); decompiled game code belongs to its rights holders.

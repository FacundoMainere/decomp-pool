# Starting a new project from this template

These steps turn the template into a contribution pool for one game. They are written so that
a person or an AI agent with this repository, the game's ROM and a GitHub account can do the
whole setup alone. GBA games built with agbcc are supported today.

The result is a repository where `functions.csv` lists the functions to decompile and
contributors claim and solve them through PRs (see `AGENTS.md`). It is not a full ROM build:
integrating `src/func_ADDR.c` into one comes later, once enough code exists.

`docs/STRATEGY.md` has the lessons behind these steps and what comes after them. Tick off the
steps in `docs/PROGRESS.md` as you go, and fill in its target line and exceptions table.

## 1. Create the repository

```sh
gh repo create OWNER/GAME-decomp --private --template OWNER/decomp-pool
sleep 10                                    # GitHub copies the template asynchronously
gh repo clone OWNER/GAME-decomp && cd GAME-decomp
git log --oneline                           # must show one "Initial commit"; if empty, wait and pull
```

Use `--public` instead for an open project: public repos get unlimited Actions minutes and
need no collaborators. Private repos spend the owner's Actions minutes (a solve run is about
a minute; the first one is longer because it builds agbcc and caches it).

## 2. Install the tools

```sh
sudo apt install binutils-arm-none-eabi gcc make git python3 python3-venv openjdk-21-jdk unzip
tools/install_agbcc.sh                      # pret agbcc, pinned, into tools/agbcc (git-ignored)
python3 -m venv ~/.venvs/pool && ~/.venvs/pool/bin/pip install clang-format==18.1.8
export PATH=~/.venvs/pool/bin:$PATH         # clang-format 18.1.8 exactly; check.py refuses others
```

Ghidra (11.x, needs JDK 17+): download the release zip from
https://github.com/NationalSecurityAgency/ghidra/releases, unzip it outside the repo, and use
`ghidra_*/support/analyzeHeadless`.

Run the tool tests once: `python3 -m unittest discover -s tools -p 'test_*.py'` must pass.

## 3. Add the ROM

Copy the ROM to `baserom.gba` at the repo root. `.gitignore` excludes `*.gba`; never commit
it, and never commit disassembly or extracted data either. Only SHA1s go into the repo.

## 4. Export the function list with Ghidra

```sh
GHIDRA=/path/to/ghidra_11.x_PUBLIC
mkdir -p /tmp/gproj
$GHIDRA/support/analyzeHeadless /tmp/gproj game -import baserom.gba \
  -loader BinaryLoader -loader-baseAddr 0x08000000 -processor ARM:LE:32:v4t \
  -scriptPath tools/ghidra -preScript GbaSetup.py -postScript ExportFuncs.py "$PWD/funcs.csv"
```

`GbaSetup.py` maps the GBA memory regions, marks the ARM entry point and seeds Thumb functions
at every 4-aligned `push {..., lr}`. `ExportFuncs.py` writes `funcs.csv`
(`start,end,thumb,name`; keep it out of git). Analysis takes a few minutes for an 8 MB ROM.
Keep `/tmp/gproj`: you can re-run scripts on the analysed project with
`-process baserom.gba -noanalysis`.

## 5. Find the end of the code region

Ghidra also "finds" thousands of functions inside graphics, sound and tables. In Medabots AX it
found 8591, and only 1671 were real code (the first 345 KB of an 8 MB ROM). Code comes first
and data after it, so you need the address where code ends:

- The code region normally ends with the library code the compiler linked in last: libgcc's
  `_call_via_rN` thunks (`bx r0; nop`, `bx r1; nop`, ...), the division routines and newlib's
  `memcpy` / `memset` / `strlen`. Search the disassembly for that run of `bx rN` thunks; the
  library functions follow it, and the end of the last one (plus padding to 4 bytes) is the end.
- Past that point, disassembly turns into nonsense: undefined instructions, Thumb code with no
  `push`/`bx` structure, and `bl` targets that point into the middle of other "functions".
- Cross-check: every real function is called from somewhere. Take a few candidate functions
  after your chosen end and confirm no `bl` from the code region reaches them.

To read any range:
`arm-none-eabi-objdump -D -b binary -marmv4t -M force-thumb --adjust-vma=0x08000000 --start-address=0x... --stop-address=0x... baserom.gba`.
objdump decodes some data as Thumb-2 instructions even with `-marmv4t`; on the GBA the only
32-bit Thumb instruction is the `bl` pair, so anything else 32-bit is data.

## 6. Generate functions.csv

```sh
python3 tools/setup.py baserom.gba funcs.csv --code-end 0x08XXXXXX
```

Each function runs from its start to the next function's start, so literal pools belong to
the function before them, and the last function runs to `--code-end`. Only thumb functions
with 4-aligned starts are kept. It writes `functions.csv` (address, size, SHA1) and sets
`rom_sha1` in `pool.toml`.

## 7. Fix the list before anyone claims from it

A wrong boundary makes a function impossible to match, and contributors would burn claims on
it. `functions.csv` is plain text: merge two rows by deleting the later one, adding its size
to the earlier one and recomputing that row's SHA1; delete rows that should not be offered.
To recompute a SHA1:
`python3 -c "import hashlib;r=open('baserom.gba','rb').read();print(hashlib.sha1(r[0xSTART-0x08000000:0xEND-0x08000000]).hexdigest())"`.

Known Ghidra mistakes, from Medabots AX:

- **Fragments**: an unreferenced Thumb start without `push {..., lr}` right after another
  function is usually the tail of the previous function. Merge it.
- **Fake starts inside big functions**: agbcc emits long branches as `bl` inside functions over
  ~2 KB. Ghidra makes the targets into functions and truncates the real one (one function of
  3440 bytes came out as 7). Suspect any "function" whose start is reached only by a `bl` from
  the function just before it.
- **Small leaf functions do exist** without a `push`; don't merge those blindly. When in doubt,
  leave it and let contributors report it (see the end of this file).
- **Nested functions** (save `r9`; their only caller does `mov r9, sp` before the call) must be
  one unit with their caller: merge them.

Delete rows nobody can solve in this pool:

- Hand-written assembly: `crt0`, the interrupt handler, libgcc thunks and division routines,
  BIOS call wrappers (`swi`). setup.py already drops ARM-mode functions.
- Functions built with a different compiler setup. The pool compiles everything with
  `compiler` and `cflags` from `pool.toml`. Medabots AX also had `old_agbcc` files (leaf
  functions ending in a bare `bx lr` where agbcc would `push {lr}`/`pop`), `-O1` save-library
  code (flash / EEPROM / timer), and newlib built with `-mno-thumb-interwork` (`pop {..., pc}`
  with no `bx`). If you find a group like that, remove it from the pool; per-function compiler
  settings are not supported yet.

## 8. Library aliases

agbcc calls a few library routines by name: `__divsi3`, `__modsi3`, `__udivsi3`, `__umodsi3`
for `/` and `%`, `memcpy` for struct copies, and `_call_via_r0` ... `_call_via_lr` for
indirect calls. Find each one in the ROM (they are in the library code at the end of the code
region) and add it to `[aliases]` in `pool.toml`:

```toml
[aliases]
__divsi3 = 0x080561A0
memcpy = 0x08056440
```

Without these, contributors cannot match any function that divides or copies structs.

## 9. Sanity-check a few functions

Pick three small functions from `python3 tools/pool.py free 50` and solve them yourself:
`python3 tools/check.py --asm ADDR`, write `src/func_ADDR.c`, then
`python3 tools/check.py ADDR src/func_ADDR.c` until it prints `MATCH`. This proves the
boundaries, `rom_sha1`, `cflags` and the aliases are right. Do not commit these files yet:
step 11 submits them through the pool.

## 10. Configure and publish

Edit the README title and description for the game, then commit and push:

```sh
git add functions.csv pool.toml README.md docs/PROGRESS.md
git commit -m "Pool for GAME"
git push
tools/configure_repo.sh OWNER/GAME-decomp CONTRIBUTOR1 CONTRIBUTOR2
```

`configure_repo.sh` adds the `decomp-pool` topic. On a private repo it also allows workflows on
fork PRs (with write tokens, no secrets, no approval) and gives the listed GitHub users read
access, so they can fork; they must accept the invitation. Read access cannot push: all
contributions still go through PRs. Nothing else needs configuring: the workflows request
their own token permissions.

## 11. Test the loop before inviting people

From the owner account, with branches of the repo itself (PRs from the repo's own branches run
the same workflow). `ME=$(gh api user -q .login)`.

1. **Claim**: on a branch, `echo "$ME $(date -u +%Y-%m-%dT%H:%M:%SZ)" > pool/claims/ADDR`,
   commit, push, `gh pr create --fill`. The "Pool" workflow should merge it within a couple
   of minutes (`gh pr view N --json state`).
2. **Solve**: on a new branch from the updated main, add the `src/func_ADDR.c` from step 9 and
   `git rm pool/claims/ADDR`, open a PR. It should be compiled, verified and merged.
3. **Reject**: open a claim PR for an address not in `functions.csv`. The bot should comment
   "not in functions.csv" and leave it open. Close it.
4. **Release**: claim another function, then open a PR that only deletes its claim file.
5. **Expire**: set `claim_hours = 0` in `pool.toml` on main, claim a function, run
   `gh workflow run expire.yml`, and check that the claim is gone and `pool/strikes/<login>`
   exists. Then restore `claim_hours` and delete the strike file.

Then ask one contributor to do steps 1 and 2 from their fork: that checks the fork settings.
If their "Pool" run cannot merge because of permissions, re-run `tools/configure_repo.sh` and
check Settings -> Actions -> General -> "Fork pull request workflows in private repositories".

## How contributors report problems

If a function is impossible because of a bad boundary, contributors release it and either
open an issue ("bogus start at X", "missed start at X", "not a function at X") or a PR that
changes only `functions.csv`. Fix issues on main yourself; their next claim sees the new
list.

Boundary PRs need the ROM to check, so CI leaves them alone ("manual"). Review them with the
boundary routine on your machine, which has `baserom.gba`:

```sh
python3 tools/boundaries.py routine --dry-run   # show the verdicts only
python3 tools/boundaries.py routine             # merge the proven ones, comment on the rest
```

A PR is merged only when:
- its rows cover the same bytes as before;
- every changed row has the ROM's SHA1;
- it touches no solved or claimed function;
- the code backs up every moved boundary: a removed start is a literal-pool word loaded by
  the function before it; an added start is called from another function, is a function
  pointer in the code, or is where code resumes after such a pool; and no changed row hides
  a call target.

Anything else (removing rows, merging fake starts or nested functions) gets a comment with
the reasons and waits for you. Each PR head is commented on once, so the routine can run
often. Run it periodically, for example from cron every three hours (cron has no keyring,
so give `gh` a token):

```
0 */3 * * * cd /path/to/GAME-decomp && GH_TOKEN=... python3 tools/boundaries.py routine >> boundaries.log 2>&1
```

Or leave a Claude Code session running
`/loop 3h run python3 tools/boundaries.py routine and summarise what it merged`.

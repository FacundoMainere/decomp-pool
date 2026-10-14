# Strategy and findings

How a GBA ROM goes from nothing to matching C with LLM agents, and what we learned doing it.
The numbers come from Medabots AX (USA), which reached 100% matching in about 2.5 days of wall
time with up to 8 agents. Maintainers: add your game's findings at the end of this file.

## Pipeline

1. **Function list.** Use Ghidra headless to find the functions, cut the list to the real code
   region and fix the boundaries (`docs/NEW_PROJECT.md` steps 4-7). This is the step that
   matters most: a wrong boundary can never match, and agents burn claims on it.
2. **Matching, one function at a time.** This is the pool: claim, solve, verify, merge.
3. **Near-misses.** Run decomp-permuter on the closest attempts, then do an endgame pass with
   compiler dumps (see below).
4. **After the pool.** Integrate `src/func_*.c` into a full ROM build, then unify the headers,
   group the code by subsystem and name things. This is outside the template; the lessons are
   at the end of this file.

## Running agents

- **Stream.** Long-lived agents that each claim the next function beat batches. A batch has to
  wait for its slowest agent.
- **Time box.** Stop after about 15 minutes or 25 attempts, release the function and move on.
  Every claim must be attempted. Medabots agents that skipped large functions left them
  claimed for nobody.
- **Shared knowledge needs a feed.** Agents read `docs/MATCHING_TIPS.md` once at the start.
  It only grows if agents report new tricks, so they open issues and a maintainer curates
  them into the file. When merging duplicate tips, keep the lowest ID.
- **Tip usage reports are noisy.** Agents under-credit tips: T06 was nearly purged with 1
  credited help, but 47 solved files used it. Check a tip against the solved source before
  removing it.
- **Boundary reports come from agents.** Auto-detecting missed starts from `bl` targets gave
  216 candidates, mostly bogus and none of the real misses. Agent reports
  ("missed start at X", "bogus start at X") found them.
- **Starts can be a few bytes off** (one reported start was 4 bytes late, inside the
  prologue). Agents notice it when their C matches bytes shifted by N.

### Model and effort

Measured as committed bytes per cost unit, with list-price dollars as a proxy for subscription
quota. Cache reads dominate agent traffic (about 23M cache-read tokens vs 0.3M others for one
agent), so a token count that skips them badly misstates cost.

- **Fresh small functions:** Opus at medium or low effort was cheapest per byte.
- **Retried near-misses:** Opus high won clearly (about 3450 vs 2450 bytes per agent-hour for
  medium).
- **Sonnet:** competitive early on, but it tended to skip large functions, which flattered its
  per-function numbers.
- **Early leads regress.** One lucky agent gave Sonnet medium a 3.7x lead that fell to 0.75x
  once it had 4 agents of data. Function size confounds bytes-based scores across runs, so
  compare settings within a run.
- **"Default" effort is not a setting.** An agent with no effort set inherits the session's
  effort. Check what it resolves to before comparing it with the others.
- **It gets harder over time.** Claiming smallest-first (the pool's order) leaves the hardest
  functions for last: throughput fell from about 4000 to 2300 bytes per agent-hour by 40%.

### Endgame

The last ~10 functions had each been retried 5+ times.

- **Agent settings.** Low-effort agents stop after a few variants and add nothing. Use medium
  or high effort, one agent per function, with a long time box.
- **Read the compiler, not more variants.** The final fixes came from dumping agbcc's RTL
  (`-da`, `-dL`, `-dG`, `.greg`), or from an instrumented agbcc (`fprintf` in reload1.c
  `allocate_reload_reg` and global.c `find_reg`), to read allocation priorities. The tricks
  that worked are in MATCHING_TIPS.md under "Compiler internals" and "Register allocation".
- **Next time:** give endgame agents the dumps from the start.

### decomp-permuter (agbcc)

- **Compile script:** `cpp | agbcc | append ".align 2, 0" | as`.
- **Preprocessing:** preprocess `base.c` with the project's include paths first, since the
  permuter runs a bare cpp.
- **Target object:** build `target.o` from the ROM bytes. Use `.inst.n` per halfword, a
  symbolic `bl func_X`, and `.4byte SYM` wherever the compiled base has an ABS32 reloc whose
  name matches the ROM word. Otherwise every literal pool word counts in the score (605 vs 5).
- **Timeouts:** run it in its own process group and kill the group on timeout, since workers
  outlive the parent.
- **Results:** a function an agent had given up on matched in under 5 seconds. On 91 queued
  near-misses, it found 5 matches.

## Compiler and toolchain gotchas

- **Compiler flags.** Try `-fprologue-bugfix` globally on day one; it is in this template's
  `cflags`. In Medabots AX every default file compiled identically with or without it. Every
  file first attributed to old_agbcc matched agbcc plus the flag: a leaf ending in a bare
  `bx lr` (no `push {lr}`) is that flag, not old_agbcc. The real per-file exceptions were
  `-O1` (Nintendo flash/EEPROM/timer libraries and the sound driver) and
  `-mno-thumb-interwork` (newlib).
- **Rule out the wrong flavour early.** When a new flag set was found, about 20 saved
  attempts matched unchanged. Re-run old attempts with it before spending agents on them.
- **Library names.** agbcc calls `memcpy` by name for struct copies, and the libgcc routines
  by name for `/`, `%` and indirect calls (`_call_via_rN`). Alias them to the ROM copies
  early (`[aliases]` in `pool.toml`).
- **objdump.** binutils 2.40 decodes Thumb-2 even with `-marmv4t`. On ARMv4T the only 32-bit
  Thumb instruction is the `bl` pair; anything else 32-bit is data.
- **Ghidra boundaries.** See `docs/NEW_PROJECT.md` step 7: fragments, fake starts inside
  functions over ~2 KB, nested functions, and real small leaves that must not be merged.
- **The last function in the code region** owns the tail padding up to where data starts.

## After the pool: a full build

What it took in Medabots AX, once every function had matching C:

- **Build gotchas.**
  - ld pads each object to 4 bytes with a Thumb nop, so file boundaries must be 4-aligned.
  - The original zero-pads between functions (`.align 2, 0`).
  - Absolute function symbols need `.thumb_set`: with `--defsym`, ld treats the target as
    ARM and adds a veneer.
  - Data symbols (`gUnk_XXXXXXXX`) come from a generated linker script.
  - Gate every commit on a clean `make compare` with `&&`, never `;` or a pipeline: both
    hide failures, and a broken tree was pushed each way.
- **Headers.** One-function-per-file matching leaves every global with many partial "view"
  structs: 145 for the main state global, and 4215 of 5387 local prototypes disagreed with
  their definitions. The pass that worked:
  1. One worktree agent per cross-cutting global builds the canonical struct and fixes every
     user.
  2. One agent per subsystem (contiguous address range) groups files and writes its header.
  3. Callers include the callee headers instead of local prototypes.
- **Header techniques.**
  - To find which fields of a view are used, blank its body (`u8 zz[SIZE];`) and collect the
    compiler's "no member named" errors.
  - Pick each field's signedness by experiment: flip it and count the functions that start or
    stop matching. Majority votes were often wrong.
  - Compare per file: diff each recompiled `.s` per function, ignoring local label numbers.
  - agbcc -O2 uses type-based alias analysis, so turning raw `u8 *` accesses into struct
    fields can reorder loads and stores. Convert one function at a time.
- **Merging parallel branches.** Most conflicts are adjacent declaration lines; resolve them
  mechanically with a token-level 3-way merge. Add header dependencies (`-MMD -MP`) once
  headers carry real types: an incremental build reused stale objects after a header changed
  and printed OK on a broken tree.
- **Naming.**
  1. Read-only agents, one per chunk, propose names as JSON maps.
  2. One script applies all the maps in one pass.
  3. Worktree agents rename fields and locals.

  Renames never change code, so `make compare` checks every step.
- **A second version** of the same game (Medabots AX Rokusho) took about an hour:
  1. Align functions with `bl` immediates and pool pointers masked: 1679 of 1688 were
     identical.
  2. Map data symbols through the literal pools of the identical functions.
  3. Put the few differences behind `#ifdef`.

## Findings for this game

Maintainers: add what you learn here (boundary surprises, compiler flavours, throughput).

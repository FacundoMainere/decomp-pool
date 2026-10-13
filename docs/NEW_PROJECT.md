# Starting a new project from this template

These steps turn this template into a decompilation of your game. GBA games built with
agbcc are supported today.

1. On GitHub, **Use this template -> Create a new repository**, public. Clone it.
2. Install the tools: `binutils-arm-none-eabi`, `tools/install_agbcc.sh`,
   `pip install clang-format==18.1.8`, and Ghidra (11.x) for the function list.
3. Put your ROM at `baserom.gba` (git-ignored; never commit it).
4. Export the functions with Ghidra headless:

       analyzeHeadless /tmp/gproj game -import baserom.gba -loader BinaryLoader \
         -loader-baseAddr 0x08000000 -processor ARM:LE:32:v4t \
         -scriptPath tools/ghidra -postScript ExportFuncs.py funcs.csv

5. **Find the code region.** Ghidra also "finds" thousands of functions inside graphics and
   other data. Find where the code ends (for example the last function reached by a `bl`
   from earlier code) and pass it as `--code-end`. In Medabots AX, 8591 functions were found
   and only 1671 were real code, ending at 0x08056536.
6. Run `python3 tools/setup.py baserom.gba funcs.csv --code-end 0x08XXXXXX`. Each function
   runs to the next function's start, so its literal pool is included. It keeps thumb
   functions with 4-aligned starts, writes `functions.csv` (addresses, sizes and SHA1s: no
   ROM bytes) and sets `rom_sha1` in `pool.toml`.
7. **Check boundaries.** Spot-check functions with `tools/check.py --asm ADDR`: a function
   should start with a `push` (or be a small leaf) and end with `bx`/`pop {pc}` and its literal
   pool. Fragments Ghidra split in two must be merged by hand in `functions.csv`; nobody can
   match half a function. Functions CI should never offer (ARM code, library code) are just
   left out of `functions.csv`.
8. If agbcc emits library calls (division, `memcpy`), add each one to `[aliases]` in
   `pool.toml` with its ROM address.
9. Edit `pool.toml` limits if you want, and the README title and game name.
10. Commit and push `functions.csv` and `pool.toml`.
11. Add the `decomp-pool` topic to the repository, so discovery tools find it.
12. **Test the loop from a second GitHub account** before announcing it: a claim PR, a solve
    PR (decompile one small function yourself), a release PR, and an expired claim (set
    `claim_hours = 0` temporarily and run the "Expire claims" workflow by hand).

No repository settings are required: the workflows request their own permissions. If your
organization restricts GitHub Actions permissions, allow read and write for this repository.

## Using the result

`src/func_ADDR.c` files are matching C, one function each, named by address. Integrating
them into a full build that produces the ROM, renaming functions and grouping them into
files are left to the maintainers, once enough code exists.

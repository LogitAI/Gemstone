# Recipes for `pip { jit }`

The toolchain plugin hands this directory to pypackpack's cross build (`localRecipes` in
`app/build.gradle.kts`; toolchain#152, format approved 2026-11-25 as toolchain Decision 16). With `jit`,
every requirement without an Android wheel on PyPI is built from its sdist for
`aarch64-linux-android` (NDK clang, API 24, CPython 3.15t). A package that builds as it is needs no
file here. A recipe is `<name>.toml` with the keys of `[tool.ppp.recipes.<name>]`:

- `patches`: paths relative to this directory, applied with `patch -p1` to the unpacked sdist;
- `env`: extra environment variables for the build.

| Package | Recipe |
|---|---|
| tokenizers, safetensors, hf-xet | none (maturin) |
| psutil 7.2.2 | `psutil.toml`: its sources reject `sys.platform == "android"`, so the patch makes it use the Linux implementation |

These are the four dependencies with no cp315t wheel (#178 lists them for the server). Agent proposal,
not confirmed: this branch (`feat/42-android-jit`, leader Decision 5, 2026-11-25) merges when toolchain
1.0.0 and PMP 3.15.0 are published.

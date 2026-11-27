"""
The Android client cross-builds the engine's native dependencies with the toolchain's `pip { jit }`
(#42, leader Decision 5, 2026-11-25). Recipes for packages that do not build as they are live in
`app/src/androidMain/pythonRecipes/` (`<name>.toml` with `patches` and `env`). The cross build
itself is the behavioural check; this guards the recipe directory, so a renamed or stray patch fails
here instead of in a five-minute cross build.
"""
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
RECIPES = ROOT / "app" / "src" / "androidMain" / "pythonRecipes"


def _recipes() -> dict[str, dict]:
    return {p.stem: tomllib.loads(p.read_text()) for p in sorted(RECIPES.glob("*.toml"))}


def test_the_build_hands_the_recipe_directory_to_jit():
    gradle = (ROOT / "app" / "build.gradle.kts").read_text()
    assert re.search(r'localRecipes\s*\{\s*add\("src/androidMain/pythonRecipes"\)\s*\}', gradle)


def test_every_recipe_names_only_existing_patches_and_every_patch_is_used():
    recipes = _recipes()
    assert recipes, "no recipe found"
    named = set()
    for name, recipe in recipes.items():
        assert set(recipe) <= {"patches", "env"}, f"{name}: unknown keys {set(recipe) - {'patches', 'env'}}"
        for patch in recipe.get("patches", []):
            assert (RECIPES / patch).is_file(), f"{name}: missing patch {patch}"
            named.add((RECIPES / patch).resolve())
    on_disk = {p.resolve() for p in (RECIPES / "patches").glob("*.patch")}
    assert on_disk == named, f"patches no recipe names: {sorted(p.name for p in on_disk - named)}"


def test_every_recipe_is_for_a_dependency_of_the_android_build_and_is_listed_in_the_readme():
    gradle = (ROOT / "app" / "build.gradle.kts").read_text()
    python_block = gradle[gradle.index("python {"):]
    dependencies = set(re.findall(r'implementation\("([^"]+)"\)', python_block))
    readme = (RECIPES / "README.md").read_text()
    for name in _recipes():
        assert name in dependencies, f"{name} is not a dependency of the python {{ }} block"
        assert f"`{name}.toml`" in readme, f"{name} is missing from the README table"


def test_the_psutil_patch_makes_android_use_the_linux_implementation():
    # psutil's setup.py and psutil/__init__.py both decide from `_common.LINUX` and otherwise stop
    # with "platform android is not supported" (red run 1 of installPythonDependenciesAndroidArm64).
    patch = (RECIPES / "patches" / "psutil-android-is-linux.patch").read_text()
    assert "+++ b/psutil/_common.py" in patch
    assert '-LINUX = sys.platform.startswith("linux")' in patch
    assert '+LINUX = sys.platform.startswith(("linux", "android"))' in patch

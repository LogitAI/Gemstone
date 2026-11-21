"""
The native desktop executable is built by compose-multiplatform-extended (SPEC S3.4, #191): the
Gradle build configures the plugin's `nativeImage { }` instead of driving native-image itself, and the
native-desktop CI job builds with `packageNativeImage` and checks the one file it writes. The job
itself (window, input, chat) is the behavioural check; this guards the wiring.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _job(workflow: str, name: str) -> str:
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", workflow, re.M | re.S)
    assert m, f"no job {name!r}"
    return m.group(1)


def test_the_build_uses_the_plugin_and_keeps_only_gemstone_parts():
    gradle = (ROOT / "app" / "build.gradle.kts").read_text()
    assert re.search(r"^\s*nativeImage \{", gradle, re.M)
    for own in ("nativeCompile", "nativeDist", "metadataCopy"):
        assert not re.search(rf"val {own} by tasks", gradle), f"{own} is the plugin's job now"
    assert "-agentlib:native-image-agent" not in gradle, "the tracing agent is runNativeImageAgent"
    for kept in ("generateNativeSerializerConfig", "generateNativeResourceConfig", "compileNativeResources"):
        assert re.search(rf"val {kept} by tasks", gradle), f"{kept} is Gemstone's and stays"
    assert re.search(r'name == "packageNativeImage" \}\.configureEach \{\s*dependsOn\(generateNativeSerializerConfig, '
                     r'generateNativeResourceConfig, compileNativeResources\)', gradle)


def test_ci_builds_with_package_native_image_and_checks_the_one_file():
    job = _job((ROOT / ".github" / "workflows" / "test.yml").read_text(), "native-desktop")
    assert ":app:packageNativeImage" in job and "nativeDist" not in job
    assert "bellsoft-liberica-vm-full-openjdk25" in job and "ilammy/msvc-dev-cmd" in job
    assert "build-skiko-static-jvm.sh" in job and "ref: v1.11.1-ext.5" in job
    assert 'app/build/compose/native-image/main' in job
    assert "ProductVersion" in job and "permonitorv2" in job
    assert "chats.json" in job  # the chat check stays

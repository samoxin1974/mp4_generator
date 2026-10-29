#!/usr/bin/env python3
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


APP_NAME = "MP4 Generator"
DMG_NAME = "MP4-Generator-Apple-Silicon.dmg"
ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
ICON_SOURCE = ROOT / "mp4-generator.png"
ICONSET_DIR = BUILD_DIR / "mp4-generator.iconset"
ICON_ICNS = BUILD_DIR / "mp4-generator.icns"
APP_BUNDLE = DIST_DIR / f"{APP_NAME}.app"
MEDIA_TOOL_NAMES = ("ffmpeg", "ffprobe", "ffplay")
HOMEBREW_PREFIX = Path("/opt/homebrew")
SYSTEM_DEP_PREFIXES = (
    "/System/Library/",
    "/usr/lib/",
)
TOOLS = {
    "codesign": Path("/usr/bin/codesign"),
    "file": Path("/usr/bin/file"),
    "hdiutil": Path("/usr/bin/hdiutil"),
    "install_name_tool": Path("/usr/bin/install_name_tool"),
    "lipo": Path("/usr/bin/lipo"),
    "otool": Path("/usr/bin/otool"),
    "xattr": Path("/usr/bin/xattr"),
}


def run(command, *, cwd=ROOT, capture=False, log=True):
    if log:
        print("+ " + " ".join(str(part) for part in command))
    if capture:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    subprocess.run(command, cwd=cwd, check=True)
    return ""


def require_tools():
    if platform.machine() != "arm64":
        raise SystemExit("This build is Apple Silicon-only; run it on an arm64 Mac.")

    for name, path in TOOLS.items():
        if not path.exists():
            raise SystemExit(f"Required macOS tool not found: {name} ({path})")

    run([sys.executable, "-m", "PyInstaller", "--version"], capture=True)


def find_media_tool(name):
    candidates = [HOMEBREW_PREFIX / "bin" / name]
    discovered = shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))

    for candidate in candidates:
        if candidate.exists():
            real_candidate = candidate.resolve()
            archs = run([TOOLS["lipo"], "-archs", str(real_candidate)], capture=True).split()
            if "arm64" in archs:
                return real_candidate

    raise SystemExit(f"Could not find an arm64 {name} binary.")


def generate_icon():
    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit("Pillow is required to generate the .icns app icon.") from exc

    if not ICON_SOURCE.exists():
        raise SystemExit(f"Icon source not found: {ICON_SOURCE}")

    BUILD_DIR.mkdir(exist_ok=True)
    if ICONSET_DIR.exists():
        shutil.rmtree(ICONSET_DIR)

    square = BUILD_DIR / "mp4-generator-square.png"
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    source = Image.open(ICON_SOURCE).convert("RGBA")
    source.thumbnail((1024, 1024), resampling)

    canvas = Image.new("RGBA", (1024, 1024), (32, 33, 38, 255))
    canvas.alpha_composite(source, ((1024 - source.width) // 2, (1024 - source.height) // 2))
    canvas.save(square)

    if ICON_ICNS.exists():
        ICON_ICNS.unlink()
    canvas.save(
        ICON_ICNS,
        sizes=[
            (16, 16),
            (32, 32),
            (64, 64),
            (128, 128),
            (256, 256),
            (512, 512),
            (1024, 1024),
        ],
    )


def build_app():
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--windowed",
            "--target-architecture",
            "arm64",
            "--name",
            APP_NAME,
            "--icon",
            str(ICON_ICNS),
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR / "pyinstaller"),
            "--specpath",
            str(BUILD_DIR),
            "--add-data",
            f"{ICON_SOURCE}:.",
            "--add-data",
            f"{ICON_ICNS}:.",
            str(ROOT / "mp4-generator.py"),
        ]
    )


def otool_deps(path, *, include_dylib_id=False):
    output = run([TOOLS["otool"], "-L", str(path)], capture=True, log=False)
    deps = []
    lines = output.splitlines()[1:]
    for index, line in enumerate(lines):
        dep = line.strip().split(" ", 1)[0]
        if not dep:
            continue
        if path.suffix == ".dylib" and index == 0 and not include_dylib_id:
            continue
        deps.append(dep)
    return deps


def is_external_homebrew_dep(dep):
    if dep.startswith(SYSTEM_DEP_PREFIXES):
        return False
    return dep.startswith(str(HOMEBREW_PREFIX)) or dep.startswith("@rpath/") or dep.startswith("@loader_path/")


def resolve_external_dep(dep, current_path):
    if dep.startswith(str(HOMEBREW_PREFIX)):
        return Path(dep).resolve()

    if dep.startswith("@loader_path/"):
        candidate = current_path.parent / dep.removeprefix("@loader_path/")
        if candidate.exists():
            return candidate.resolve()

    if dep.startswith("@rpath/"):
        filename = dep.removeprefix("@rpath/")
        candidates = [
            current_path.parent / filename,
            HOMEBREW_PREFIX / "lib" / filename,
        ]
        candidates.extend(HOMEBREW_PREFIX.glob(f"Cellar/*/*/lib/{filename}"))

        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

    return None


def collect_homebrew_deps(start_paths):
    pending = list(start_paths)
    collected = {}

    while pending:
        current = pending.pop()
        for dep in otool_deps(current):
            if not is_external_homebrew_dep(dep):
                continue

            real_path = resolve_external_dep(dep, current)
            if real_path is None:
                raise SystemExit(f"Could not resolve dependency {dep} referenced by {current}")

            filename = Path(dep).name
            prior = collected.get(filename)
            if prior and prior != real_path:
                raise SystemExit(
                    f"Two Homebrew libraries share the same filename: {prior} and {real_path}"
                )

            if filename not in collected:
                collected[filename] = real_path
                pending.append(real_path)

    return collected


def patch_dependencies(target, original_names, replacement_prefix):
    deps = set(otool_deps(target))
    for original_name in sorted(original_names):
        matching_deps = [dep for dep in deps if dep.endswith(f"/{original_name}")]
        for dep in matching_deps:
            run(
                [
                    TOOLS["install_name_tool"],
                    "-change",
                    dep,
                    f"{replacement_prefix}/{original_name}",
                    str(target),
                ],
                capture=True,
                log=False,
            )


def bundle_media_tools():
    resources_dir = APP_BUNDLE / "Contents" / "Resources"
    frameworks_dir = APP_BUNDLE / "Contents" / "Frameworks"
    bin_dir = resources_dir / "bin"
    ffmpeg_libs_dir = frameworks_dir / "ffmpeg-libs"

    if ffmpeg_libs_dir.exists():
        shutil.rmtree(ffmpeg_libs_dir)

    bin_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_libs_dir.mkdir(parents=True, exist_ok=True)

    media_sources = [find_media_tool(name) for name in MEDIA_TOOL_NAMES]
    media_targets = []
    for source in media_sources:
        target = bin_dir / source.name
        shutil.copy2(source, target)
        target.chmod(0o755)
        media_targets.append(target)

    deps = collect_homebrew_deps(media_sources)
    for filename, source in sorted(deps.items()):
        target = ffmpeg_libs_dir / filename
        shutil.copy2(source, target)
        target.chmod(0o755)

    copied_libs = sorted(ffmpeg_libs_dir.glob("*.dylib"))
    original_names = set(deps)

    for target in media_targets:
        patch_dependencies(
            target,
            original_names,
            "@loader_path/../../Frameworks/ffmpeg-libs",
        )

    for target in copied_libs:
        run(
            [TOOLS["install_name_tool"], "-id", f"@loader_path/{target.name}", str(target)],
            capture=True,
            log=False,
        )
        patch_dependencies(target, original_names, "@loader_path")

    return media_targets


def macho_files(root):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            info = run([TOOLS["file"], str(path)], capture=True, log=False)
        except subprocess.CalledProcessError:
            continue
        if "Mach-O" in info:
            yield path


def verify_no_external_build_refs():
    bad_refs = []
    for path in macho_files(APP_BUNDLE):
        try:
            output = run([TOOLS["otool"], "-L", str(path)], capture=True, log=False)
        except subprocess.CalledProcessError:
            continue
        for bad_prefix in ("/opt/homebrew", "/opt/anaconda3"):
            if bad_prefix in output:
                bad_refs.append((path, bad_prefix))

    if bad_refs:
        details = "\n".join(f"{path}: still references {prefix}" for path, prefix in bad_refs)
        raise SystemExit(f"App bundle is not standalone:\n{details}")


def ad_hoc_sign_app():
    run([TOOLS["xattr"], "-cr", str(APP_BUNDLE)])
    for path in macho_files(APP_BUNDLE):
        run([TOOLS["codesign"], "--force", "--sign", "-", str(path)], capture=True, log=False)
    run([TOOLS["codesign"], "--force", "--deep", "--sign", "-", str(APP_BUNDLE)])


def smoke_test_media_tools(media_targets):
    for target in media_targets:
        run([str(target), "-version"], capture=True)


def create_dmg():
    dmg_path = DIST_DIR / DMG_NAME
    staging_dir = BUILD_DIR / "dmg-staging"

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    shutil.copytree(APP_BUNDLE, staging_dir / APP_BUNDLE.name, symlinks=True)
    os.symlink("/Applications", staging_dir / "Applications")

    if dmg_path.exists():
        dmg_path.unlink()

    run(
        [
            TOOLS["hdiutil"],
            "create",
            "-volname",
            APP_NAME,
            "-srcfolder",
            str(staging_dir),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ]
    )
    run([TOOLS["xattr"], "-cr", str(dmg_path)])
    return dmg_path


def main():
    require_tools()
    generate_icon()
    build_app()
    media_targets = bundle_media_tools()
    verify_no_external_build_refs()
    ad_hoc_sign_app()
    smoke_test_media_tools(media_targets)
    dmg_path = create_dmg()
    print(f"Created {dmg_path}")


if __name__ == "__main__":
    main()

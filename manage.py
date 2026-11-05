#!/usr/bin/env python3
"""Build and install a pinned Codex release with the familiar-footer patch."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "patches/familiar-footer.patch"
SETTINGS = ROOT / "local.json"
PIN = ROOT / "upstream.json"
UPSTREAM = "https://github.com/openai/codex.git"
HELPERS = ("codex-code-mode-host", "codex-exec-server")
BUILD_ARGS = ["--bin", "codex", "--profile", "dev", "--config", "profile.dev.debug=0",
              "--config", "profile.dev.incremental=false"]


def run(*args, cwd=None, capture=False, env=None):
    return subprocess.run([str(a) for a in args], cwd=cwd, check=True, env=env,
                          stdout=subprocess.PIPE if capture else None,
                          text=capture).stdout


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def patch_paths():
    return re.findall(r"^diff --git a/(.+) b/", PATCH.read_text(), re.MULTILINE)


def source_diff(source):
    return run("git", "diff", "--binary", "--full-index", "--", *patch_paths(),
               cwd=source, capture=True)


def config():
    if not SETTINGS.exists():
        raise RuntimeError("Run configure first; see README.md for the required paths.")
    return {key: Path(value).expanduser().resolve() for key, value in read_json(SETTINGS).items()}


def configure(args):
    values = {key: str(Path(getattr(args, key)).expanduser().resolve())
              for key in ("source", "workspace", "destination", "command_dir")}
    if args.helpers:
        values["helpers"] = str(Path(args.helpers).expanduser().resolve())
    if SETTINGS.exists() and read_json(SETTINGS) != values:
        backup = SETTINGS.with_name("local.json.before-" + stamp())
        shutil.copy2(SETTINGS, backup)
    write_json(SETTINGS, values)
    SETTINGS.chmod(0o600)
    print("Saved private paths in ignored local.json.")


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def tools():
    if platform.system() != "Darwin":
        raise RuntimeError("This installer currently supports macOS.")
    env = os.environ.copy()
    cargo_dir = Path.home() / ".cargo/bin"
    env["PATH"] = str(cargo_dir) + os.pathsep + env.get("PATH", "")
    env["CARGO_NET_GIT_FETCH_WITH_CLI"] = "true"
    for tool in ("git", "cargo", "cmake"):
        if not shutil.which(tool, path=env["PATH"]):
            raise RuntimeError(f"Install {tool} before building; see README.md.")
    return env


def ensure_source(cfg, pin):
    source = cfg["source"]
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--depth", "1", "--branch", "rust-v" + pin["version"],
            UPSTREAM, source)
    if not (source / ".git").exists():
        raise RuntimeError(f"Not a Git checkout: {source}")
    try:
        run("git", "cat-file", "-e", pin["commit"] + "^{commit}", cwd=source, capture=True)
    except subprocess.CalledProcessError:
        run("git", "fetch", "--depth", "1", UPSTREAM, pin["commit"], cwd=source)
    return source


def prepare(cfg, pin):
    source = ensure_source(cfg, pin)
    workspace = cfg["workspace"]
    workspace.mkdir(parents=True, exist_ok=True)
    patch_hash = digest(PATCH)
    build = workspace / (pin["version"] + "-" + pin["commit"][:8] + "-" + patch_hash[:12])
    marker = build / ".codex-ui-build.json"
    if build.exists():
        if not marker.exists():
            raise RuntimeError(f"An unfinished build checkout needs review: {build}")
        saved = read_json(marker)
        head = run("git", "rev-parse", "HEAD", cwd=build, capture=True).strip()
        actual = hashlib.sha256(source_diff(build).encode()).hexdigest()
        if head != pin["commit"] or saved != {"patch": patch_hash, "diff": actual}:
            raise RuntimeError(f"Build checkout has additional changes; leaving it intact: {build}")
    else:
        run("git", "worktree", "add", "--detach", build, pin["commit"], cwd=source)
        try:
            run("git", "apply", "--check", PATCH, cwd=build)
            run("git", "apply", PATCH, cwd=build)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                f"The patch needs porting to {pin['version']}. Checkout: {build}. "
                "The installed version is unchanged.") from error
        write_json(marker, {"patch": patch_hash,
                           "diff": hashlib.sha256(source_diff(build).encode()).hexdigest()})
    return build


def build_binary(cfg, pin, reuse=False):
    if reuse:
        source = cfg["source"]
        head = run("git", "rev-parse", "HEAD", cwd=source, capture=True).strip()
        if head != pin["commit"] or source_diff(source) != PATCH.read_text():
            raise RuntimeError("--reuse-build requires the configured source to match the pinned patch exactly.")
        binary = source / "codex-rs/target/debug/codex"
        if not binary.is_file() or any((source / p).stat().st_mtime > binary.stat().st_mtime
                                       for p in patch_paths()):
            raise RuntimeError("The existing executable is missing or older than the patched source.")
        return binary
    env = tools()
    source = prepare(cfg, pin)
    # Reuse dependency artifacts between release worktrees.
    env["CARGO_TARGET_DIR"] = str(cfg["workspace"] / "target")
    run("cargo", "build", *BUILD_ARGS, cwd=source / "codex-rs", env=env)
    return cfg["workspace"] / "target/debug/codex"


def release_assets(version):
    url = "https://api.github.com/repos/openai/codex/releases/tags/rust-v" + version
    request = urllib.request.Request(url, headers={"User-Agent": "codex-ui-installer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return {asset["name"]: asset for asset in json.load(response)["assets"]}


def matching_helpers(cfg, version):
    directory = cfg.get("helpers")
    if directory and all((directory / name).is_file() for name in (*HELPERS, "codex")):
        found = run(directory / "codex", "--version", capture=True).strip()
        if found == "codex-cli " + version:
            return directory
    arch = {"arm64": "aarch64", "x86_64": "x86_64"}.get(platform.machine())
    if not arch:
        raise RuntimeError("Unsupported macOS architecture: " + platform.machine())
    assets = release_assets(version)
    cache = cfg["workspace"] / ("helpers-" + version + "-" + arch)
    cache.mkdir(parents=True, exist_ok=True)
    for name in HELPERS:
        asset = assets[name + "-" + arch + "-apple-darwin.tar.gz"]
        expected = asset.get("digest")
        if not expected or not expected.startswith("sha256:"):
            raise RuntimeError("The release lacks a SHA-256 digest for " + asset["name"])
        with tempfile.TemporaryDirectory(dir=cfg["workspace"], prefix="download-") as temporary:
            archive = Path(temporary) / "asset.tar.gz"
            with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as response:
                with archive.open("wb") as output:
                    shutil.copyfileobj(response, output)
            if digest(archive) != expected.removeprefix("sha256:"):
                raise RuntimeError("Release asset checksum mismatch: " + asset["name"])
            with tarfile.open(archive) as bundle:
                candidates = [member for member in bundle.getmembers()
                              if member.isfile() and Path(member.name).name in
                              (name, name + "-" + arch + "-apple-darwin")]
                if len(candidates) != 1:
                    raise RuntimeError("Unexpected helper archive layout: " + asset["name"])
                with bundle.extractfile(candidates[0]) as stream, (cache / name).open("wb") as output:
                    shutil.copyfileobj(stream, output)
            (cache / name).chmod(0o755)
    return cache


def replace_text(path, text, mode=0o755):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace a symbolic link: {path}")
    if path.exists() and path.read_text() != text:
        shutil.copy2(path, path.with_name(path.name + ".before-" + stamp()))
    # Replace only a complete file, preserving any existing version as a backup.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + "-")
    try:
        with os.fdopen(fd, "w") as output:
            output.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def set_link(path, target):
    if path.exists() and not path.is_symlink():
        raise RuntimeError(f"Expected a release pointer, not a regular file: {path}")
    temporary = path.with_name("." + path.name + "-" + stamp())
    temporary.symlink_to(target)
    os.replace(temporary, path)


def install(cfg, pin, reuse=False):
    if platform.system() != "Darwin":
        raise RuntimeError("This installer currently supports macOS.")
    binary = build_binary(cfg, pin, reuse)
    helpers = matching_helpers(cfg, pin["version"])
    destination = cfg["destination"]
    release = destination / "releases" / (pin["version"] + "-" + stamp())
    (release / "bin").mkdir(parents=True)
    for src, name in [(binary, "codex-ui"), *((helpers / name, name) for name in HELPERS)]:
        shutil.copy2(src, release / "bin" / name)
        (release / "bin" / name).chmod(0o755)
    write_json(release / "manifest.json", {
        **pin, "patch_sha256": digest(PATCH),
        "files": {name: digest(release / "bin" / name) for name in ("codex-ui", *HELPERS)},
    })
    current = destination / "current"
    previous = os.readlink(current) if current.is_symlink() else None
    if previous is None and (destination / "bin/codex-ui").is_file():
        previous = "."  # Preserve the initial flat installation as a rollback target.
    # Refuse unexpected pointers before touching launcher files.
    for pointer in (current, destination / "previous"):
        if pointer.exists() and not pointer.is_symlink():
            raise RuntimeError(f"Release pointer is a regular file: {pointer}")
    launcher = (ROOT / "templates/codex-ui").read_text()
    replace_text(destination / "codex-ui", launcher)
    # Quote the configured path, so spaces and shell metacharacters stay literal.
    import shlex
    command = "#!/bin/sh\nexec " + shlex.quote(str(destination / "codex-ui")) + ' "$@"\n'
    replace_text(cfg["command_dir"] / "codex-ui", command)
    if previous:
        set_link(destination / "previous", previous)
    set_link(current, str(release.relative_to(destination)))
    print("Installed:", release)
    print("Start codex-ui (or the existing cxa alias) in a fresh session.")


def rollback(cfg):
    destination = cfg["destination"]
    current, previous = destination / "current", destination / "previous"
    if not current.is_symlink() or not previous.is_symlink():
        raise RuntimeError("Two installed versions are needed for rollback.")
    old, target = os.readlink(current), os.readlink(previous)
    if not all((destination / target / "bin" / name).is_file()
               for name in ("codex-ui", *HELPERS)):
        raise RuntimeError("The previous bundle is incomplete; leaving the current version unchanged.")
    set_link(current, target)
    set_link(previous, old)
    print("Restored:", destination / target)
    print("Restart codex-ui to use that version.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    setup = commands.add_parser("configure", help="Save private local installation paths")
    for field in ("source", "workspace", "destination"):
        setup.add_argument("--" + field, required=True)
    setup.add_argument("--command-dir", default=str(Path.home() / ".local/bin"))
    setup.add_argument("--helpers", help="Matching official Codex bin directory; otherwise helpers are downloaded")
    build = commands.add_parser("install", help="Build and install the pinned release")
    build.add_argument("--reuse-build", action="store_true", help="Install an already built, exactly matching source checkout")
    update = commands.add_parser("update", help="Port, build, and install a requested release")
    update.add_argument("version", help="Explicit release version, for example 0.166.0")
    commands.add_parser("rollback", help="Switch to the previous installed bundle")
    args = parser.parse_args()
    if args.action == "configure":
        configure(args)
        return
    cfg = config()
    if args.action == "rollback":
        rollback(cfg)
        return
    pin = read_json(PIN)
    if args.action == "update":
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?", args.version):
            raise RuntimeError("Use an explicit release version, not a branch or 'latest'.")
        source = ensure_source(cfg, pin)
        run("git", "fetch", "--depth", "1", UPSTREAM, "refs/tags/rust-v" + args.version, cwd=source)
        commit = run("git", "rev-parse", "FETCH_HEAD^{commit}", cwd=source, capture=True).strip()
        pin = {"version": args.version, "commit": commit}
        install(cfg, pin)
        # Only advance the repo's pin and patch after the candidate was installed.
        checkout = prepare(cfg, pin)
        new_patch = source_diff(checkout)
        PATCH.write_text(new_patch)
        write_json(PIN, pin)
        print("Updated upstream.json and the patch; review and commit them together.")
    else:
        install(cfg, pin, args.reuse_build)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print("codex-ui:", error, file=sys.stderr)
        sys.exit(1)

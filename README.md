# Codex familiar footer

This repo patches a pinned Codex release to show `model@machine project · N% ctx · N% boxes → H:mm`. Quota text is dimmed, and Ctrl-D requires a second press within one second. The existing quit switch also applies to Ctrl-C. Option + Page Up / Down scroll one line; plain Page Up / Down scroll a page.

It requires macOS, Python 3.11+, Git, the Rust toolchain specified by Codex, and CMake. Install Rust with rustup and CMake with Homebrew. The first build downloads dependencies and can take several minutes; later builds reuse the cache.

Configure the paths once. The source path is a Codex clone; the installer creates it if necessary. The workspace holds separate build checkouts and cached artifacts. The destination can be a shared Dropbox application folder.

```sh
./manage.py configure --source ../codex --workspace /path/to/build-cache --destination /path/to/shared/codex
./manage.py install
```

Start `codex-ui`, or use an existing alias such as `cxa`. The installer places the command in `.local/bin` under the home directory; that directory must be on PATH. A second Mac can run the shared launcher directly or install the same command wrapper there.

To request a particular Codex release:

```sh
./manage.py update VERSION
```

The updater fetches that release, applies the patch in a separate checkout, builds it, and installs both matching helpers. It switches the shared `current` pointer only after the complete bundle is ready. If the patch does not apply, keep using the installed version and have the patch ported before retrying. Successful updates change `upstream.json` and the patch; review and commit those files together.

Run `./manage.py rollback` to restore the previous bundle, then restart Codex. Ordinary Codex updates leave the custom installation in place. Shared settings and existing conversations are preserved.

Private paths are saved in ignored `local.json`. `--helpers DIRECTORY` on configure can select an official bin directory of the matching version; otherwise verified release helpers are downloaded. `./manage.py install --reuse-build` installs an existing source checkout only when its patch, version, and executable freshness match exactly.

See [maintenance notes](docs/maintenance.md) for the patch details. The Apache 2.0 license and upstream notices are included; executable bundles stay outside this repo.

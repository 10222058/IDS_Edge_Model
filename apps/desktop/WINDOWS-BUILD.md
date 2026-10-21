# Building the Windows desktop executable

This guide builds the Windows desktop app as a single-file PyInstaller executable:
`apps/desktop/dist/IoTGatewayMonitor.exe`. Build it on Windows; PyInstaller does not
cross-compile Windows executables from Linux or macOS.

## Requirements

- Windows 10 or Windows 11, 64-bit
- Git for Windows (includes Git Bash)
- 64-bit Python 3.12 from [python.org](https://www.python.org/downloads/windows/)
  - Enable **Add python.exe to PATH** in the installer.
- Internet access to install Python dependencies

Rust, Node.js, Docker, and WSL are not required for this PyInstaller build.

## Get the source

Open Git Bash and clone the repository, or update your existing checkout:

```bash
git clone https://github.com/10222058/IDS_Edge_Model.git
cd IDS_Edge_Model
```

If the changes to package are on a specific branch, switch to that branch before
building:

```bash
git switch <branch-with-the-changes>
git pull
```

For an existing checkout, first `cd` to the repository root, then run the same
`git switch` and `git pull` commands. Make sure the checked-out revision contains
the source changes you intend to distribute.

## Build

Run these commands from the repository root in Git Bash:

```bash
python --version
python -c "import struct; print(f'{struct.calcsize(\"P\") * 8}-bit Python')"
python -m venv apps/desktop/build/venv
source apps/desktop/build/venv/Scripts/activate
bash apps/desktop/packaging/build_windows.sh
```

Confirm the Python version is 3.12 and the architecture is 64-bit. The build script
installs PyInstaller and the desktop/control-service dependencies into the active
Python environment, prepares the Windows icon, creates the executable, and writes a
SHA-256 checksum.

Expected outputs:

```text
apps/desktop/dist/IoTGatewayMonitor.exe
apps/desktop/dist/SHA256SUMS-windows.txt
```

The first build can take several minutes. Keep the virtual environment active if
you rerun the build. To start over with a clean environment, remove only
`apps/desktop/build/venv` and repeat the virtual-environment commands.

## Verify the build

In Git Bash, verify the checksum:

```bash
cd apps/desktop/dist
sha256sum -c SHA256SUMS-windows.txt
```

The expected result is:

```text
IoTGatewayMonitor.exe: OK
```

You can also calculate the hash in PowerShell:

```powershell
Get-FileHash .\apps\desktop\dist\IoTGatewayMonitor.exe -Algorithm SHA256
```

Compare the displayed hash with the value in
`apps/desktop/dist/SHA256SUMS-windows.txt`.

Before distributing, run the executable on a Windows machine. A Windows build
cannot be fully runtime-validated from a Linux host. Share the `.exe` and its
checksum together, and communicate the exact source branch/commit used.

## Common issues

- **`python` is not recognized:** Reinstall Python 3.12 and enable **Add python.exe
  to PATH**, then open a new Git Bash window.
- **Wrong Python version or 32-bit Python:** Install 64-bit Python 3.12 and check
  `python --version` and the architecture command above.
- **Dependency download fails:** Check network/proxy access, then activate the
  virtual environment and rerun the build script.
- **No executable or checksum was created:** Read the first error in the build
  output. The script exits with a nonzero status if the expected executable is
  missing.
- **SmartScreen warning:** Locally built executables are unsigned by default.
  SmartScreen may show an unrecognized-app warning. Do not advise users to bypass
  it unless they trust the source and have verified the checksum. Code signing is
  a separate release step.

## GitHub Actions alternative

The repository workflow builds Windows packages on a Windows runner. In GitHub,
open **Actions → Desktop packages (AppImage + Windows exe) → Run workflow**, select
`windows`, and choose whether to publish the package. The workflow can also be
triggered by pushing to the `windows` branch. A manual run with publishing enabled
updates the configured release tag; coordinate with the release owner before
publishing.

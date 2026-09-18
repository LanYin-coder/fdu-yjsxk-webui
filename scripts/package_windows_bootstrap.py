"""Package an allowlisted Windows source launcher, excluding all user data."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "FDUCourseHelper-Windows-x64-bootstrap.zip"
FILES = ["webui.py", "desktop.py", "requirements.txt", "requirements-desktop.txt",
         "requirements-build.txt", "FDUCourseHelper.spec", "启动选课助手.bat",
         "scripts/launch_windows.ps1", "scripts/build_windows_app.ps1", "docs/Windows.md"]
FOLDERS = ["src", "webui", "assets", "tests"]


def main():
    files = [ROOT / name for name in FILES]
    files += [file for folder in FOLDERS for file in (ROOT / folder).rglob("*")
              if file.is_file() and "__pycache__" not in file.parts
              and file.suffix not in {".pyc", ".pyo"} and file.name != ".DS_Store"]
    OUTPUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            name = "FDUCourseHelper-Windows/" + file.relative_to(ROOT).as_posix()
            if file.suffix in {".bat", ".ps1"}:
                archive.writestr(name, file.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
            else:
                archive.write(file, name)
        archive.write(ROOT / "docs/Windows.md", "FDUCourseHelper-Windows/README.md")
    with zipfile.ZipFile(OUTPUT) as archive:
        assert not any(Path(name).name in {"config.json", "cookie.txt", "instance.json"} for name in archive.namelist())
        assert archive.testzip() is None
    print(OUTPUT)


if __name__ == "__main__":
    main()

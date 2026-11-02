import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SH = ROOT / "scripts" / "scalingo" / "web.sh"


def _fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("newrelic-admin", "lightrag-gunicorn"):
        path = bin_dir / name
        path.write_text(
            "#!/bin/sh\n"
            f"printf '%s' {name} >> \"$OUT\"\n"
            'printf " %s" "$@" >> "$OUT"\n'
            'printf "\\n" >> "$OUT"\n'
        )
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _run(tmp_path: Path, env: dict[str, str], *args: str) -> str:
    out = tmp_path / "out"
    bin_dir = _fake_bin(tmp_path)
    run_env = os.environ.copy()
    run_env.pop("NEW_RELIC_LICENSE_KEY", None)
    run_env.pop("NEW_RELIC_APP_NAME", None)
    run_env.update(env)
    run_env["OUT"] = str(out)
    run_env["PATH"] = f"{bin_dir}{os.pathsep}{run_env.get('PATH', '')}"
    subprocess.run(
        ["bash", str(WEB_SH), *args],
        check=True,
        env=run_env,
        cwd=tmp_path,
    )
    return out.read_text()


def test_web_boots_gunicorn_directly_without_license_key(tmp_path: Path):
    assert _run(tmp_path, {}) == "lightrag-gunicorn --workers 1\n"


def test_web_wraps_gunicorn_with_newrelic_when_license_key_is_set(tmp_path: Path):
    line = _run(tmp_path, {"NEW_RELIC_LICENSE_KEY": "eu01xx-test"})
    assert line == "newrelic-admin run-program lightrag-gunicorn --workers 1\n"


def test_web_forwards_extra_args(tmp_path: Path):
    line = _run(tmp_path, {"NEW_RELIC_LICENSE_KEY": "eu01xx-test"}, "--workers", "1")
    assert line == "newrelic-admin run-program lightrag-gunicorn --workers 1\n"


def test_procfile_uses_the_wrapper():
    procfile = (ROOT / "Procfile").read_text()
    assert "bash scripts/scalingo/web.sh" in procfile
    assert "newrelic" in (ROOT / "pyproject.toml").read_text()

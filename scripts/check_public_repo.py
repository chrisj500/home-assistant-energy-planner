from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PATH_PATTERNS = (
    re.compile(r"(^|/)\.storage(/|$)"),
    re.compile(r"(^|/)secrets\.ya?ml$", re.IGNORECASE),
    re.compile(r"(^|/)home-assistant_v2\.db($|\.)", re.IGNORECASE),
    re.compile(r"(^|/)home-assistant\.log($|\.)", re.IGNORECASE),
    re.compile(r"(^|/)backups?(/|$)", re.IGNORECASE),
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"\.(pem|p12|pfx|key)$", re.IGNORECASE),
)

SECRET_PATTERNS = (
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Bearer token", re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~-]{20,}")),
    ("Likely API key assignment", re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+~=-]{16,}")),
)

TEXT_SUFFIXES = {
    ".py", ".yaml", ".yml", ".json", ".md", ".txt", ".toml", ".ini", ".cfg", ".sh", ".jinja", ".j2"
}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    failures: list[str] = []
    for rel in tracked_files():
        normalized = rel.replace("\\", "/")
        for pattern in FORBIDDEN_PATH_PATTERNS:
            if pattern.search(normalized):
                failures.append(f"forbidden tracked path: {normalized}")
        path = ROOT / rel
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append(f"{label} pattern found in {normalized}")

    if failures:
        print("Public repository safety check failed:")
        for failure in sorted(set(failures)):
            print(f"- {failure}")
        return 1

    print("Public repository safety check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

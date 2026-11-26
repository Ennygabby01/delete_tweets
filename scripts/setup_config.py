"""Create private configuration once, without printing secrets or replacing user values."""

import os
from pathlib import Path

from cryptography.fernet import Fernet


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    path = root / ".env"
    if path.exists():
        print("Existing .env preserved")
        return
    content = (root / ".env.example").read_text()
    content = content.replace(
        "APP_ENCRYPTION_KEY=\n", "APP_ENCRYPTION_KEY=" + Fernet.generate_key().decode() + "\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    print("Created private .env; add X_CLIENT_ID and the client secret if your X app requires it")


if __name__ == "__main__":
    main()

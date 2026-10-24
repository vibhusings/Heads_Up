"""One-time Google Calendar user authorization for the designated demo laptop."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .providers.calendar import FREEBUSY_SCOPE, READ_SCOPE, WRITE_SCOPE


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize HeadsUp to use a test Google Calendar")
    parser.add_argument("--client-secrets", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--write", action="store_true", help="request event-write permission")
    parser.add_argument("--freebusy", action="store_true", help="request free/busy permission")
    args = parser.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [READ_SCOPE]
    if args.write:
        scopes = [WRITE_SCOPE]
    if args.freebusy:
        scopes.append(FREEBUSY_SCOPE)
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secrets), scopes=scopes)
    credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True)
    args.token_file.parent.mkdir(parents=True, exist_ok=True)
    args.token_file.write_text(credentials.to_json(), encoding="utf-8")
    os.chmod(args.token_file, 0o600)
    print(f"Google Calendar authorization saved to {args.token_file}")


if __name__ == "__main__":
    main()

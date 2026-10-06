"""Write GitHub's SSH host keys to /etc/ssh/ssh_known_hosts at image build time.

Taken from GitHub's own API over HTTPS, so ssh can verify github.com with
StrictHostKeyChecking=yes without anyone mounting or trusting a known_hosts
file on first use. Not a secret: these are GitHub's public host keys.
"""

import json
import sys
import urllib.request

with urllib.request.urlopen("https://api.github.com/meta", timeout=60) as response:
    keys = json.load(response).get("ssh_keys") or []
if not keys:
    sys.exit("https://api.github.com/meta returned no ssh_keys")
with open("/etc/ssh/ssh_known_hosts", "w", encoding="utf-8") as handle:
    for key in keys:
        handle.write(f"github.com {key}\n")
print(f"github.com: {len(keys)} SSH host keys from https://api.github.com/meta")

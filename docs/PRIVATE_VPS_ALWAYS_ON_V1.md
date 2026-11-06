# Always-on PRIVATE VPS release · 2026-11-06

## Exact outcome and boundaries

This is a **real systemd deployment path** for the pre-existing Python/SQLite workbench on a Linux VPS, plus a strict Windows SSH tunnel client. The VPS process can continue running when the operator's Windows computer is switched off, and can restart after an unexpected crash/reboot. It is **not** a publicly accessible website: the only HTTP socket remains bound to `127.0.0.1:8765` on the VPS and is reached through SSH. The browser on Windows uses the same fixed `http://127.0.0.1:8765` as before.

No public reverse proxy, port-8765 firewall opening, new cloud identity, SaaS registration, externally writable model tool, background scheduler or autogeneration of SSH keys is included. This keeps the existing server's localhost Host/Origin/CSRF contract intact. Do not remove that boundary as a shortcut.

**Important:** a 24/7 service process is not a 24/7 autonomous task engine. Existing read-only agent plans can be executed by an operator, and existing server-owned reviewed batches can continue while that service stays alive; it does **not** automatically poll external sources, send email/CRM actions or accept inbound public webhooks.

## Requirements before any VPS action

- An **active Linux VPS** with systemd, SSH access and an appropriate SSH key; its actual status, OS, IP and credentials must be verified in the cloud provider console. No VPS is created or billed by the repository.
- A **non-root Linux user** for the app with an owned checkout. Do not run the app itself as root. The SSH login identity can be different from the service identity.
- **Python 3.11+**, Git, available disk/RAM, and the exact reviewed repo checkout. The service installer uses its running `sys.executable`, so invoke it with the Python interpreter you actually intend to use.
- For `--mode local-ai` or `--mode full`, an independently installed and running **Ollama** instance on the **same VPS** with a compatible model. It is NOT installed by the deployment; the Windows PC's local Ollama is not magically available to the VPS. Run `offline` when the server has insufficient memory. Model-specific resources are not yet measured.
- Ensure inbound SSH is permitted by the cloud firewall and the VPS OS firewall, preferably restricted by source IP. **Do not open port 8765**. Use public SSH keys and verify the first host-key fingerprint independently using your provider's console; the client preserves `StrictHostKeyChecking=ask`.

## On the VPS: set up once (Linux)

The following example assumes the non-root VPS SSH user is called `ubuntu` and owns `/home/ubuntu`. **Replace the username/path only after inspecting the real server.** If the account is root-only, create a separate unprivileged user and clone/provision under that account first.

In your existing SSH session as the intended non-root service user:

```bash
python3 --version                       # must print 3.11+
git clone https://github.com/naraya07pedro-spec/ai-automation-studio.git ~/ai-automation-studio
cd ~/ai-automation-studio
python3 app/local_server.py --init-owner   # choose a NEW private password (12-128 chars)
```

The provisioning program refuses to overwrite an existing owner. Never paste the password into chat or a GitHub commit. Existing user data lives in `~/.ai-automation-studio` and is NOT reset by service installation.

Inspect unit without any changes, then install it through an explicitly privileged call:

```bash
cd ~/ai-automation-studio
sudo python3 deploy/private_vps_service.py --user ubuntu --checkout "$PWD" --mode offline
sudo python3 deploy/private_vps_service.py --user ubuntu --checkout "$PWD" --mode offline --install
sudo systemctl status ai-automation-studio.service --no-pager
sudo ss -lntp | grep ':8765'
```

Only continue if `systemctl` reports **active (running)** and the listener is **127.0.0.1:8765**, not `0.0.0.0:8765`. The script refuses an existing unit file rather than silently overwriting it. It uses `Restart=on-failure`, `RestartSec=5s`, a non-root service user, `UMask=0077`, a restricted writable SQLite owner directory, and systemd sandbox controls. It does not touch the VPS firewall or create DNS/TLS.

To enable the already existing opt-in agent/source panels on a VPS with a verified model, use `--mode full` for a **fresh** installation instead of `offline`. The `full` mode exposes permitted public-read UI operations, **not** new autonomous external writes. To change an existing systemd unit, back up/review the existing configuration and perform a deliberate administrator-managed update; this installer will not overwrite it.

## From Windows: secure live access

On the Windows machine where the repository was downloaded, double-click **`CONNECT_PRIVATE_VPS.cmd`**. Enter the *SSH login* destination such as `ubuntu@203.0.113.7` (the address here is a **documentation-only example**, not your VPS). Verify the host fingerprint against the cloud provider's trusted value. The existing Windows OpenSSH client performs encrypted forwarding and may prompt for your existing SSH key passphrase.

Once both SSH and the private app respond, the script opens **http://127.0.0.1:8765**. Log in using the separate **app owner password** set on the VPS. Keep the SSH console open during use, and use Ctrl+C to disconnect. Closing Windows or ending the SSH tunnel does **not** stop the VPS systemd service, but you cannot interact with the app while disconnected. Make sure the PC's LOCAL Studio launcher is closed first because both use local port 8765.

Manual alternative:

```powershell
ssh -N -T -L 127.0.0.1:8765:127.0.0.1:8765 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=ask ubuntu@YOUR_VPS_IP
```

Then open `http://127.0.0.1:8765` in your Windows browser.

## Failure/recovery and verification

- On VPS: `sudo systemctl is-active ai-automation-studio.service`, `sudo systemctl status ai-automation-studio.service --no-pager`, and `sudo journalctl -u ai-automation-studio.service -n 50 --no-pager`. Review logs before sharing; never disclose private server/user data.
- On VPS (read-only smoke): `curl --noproxy '*' -fsS http://127.0.0.1:8765/ -o /dev/null`. An HTTP 200 login page proves the HTTP handler responded, not AI inference accuracy or business integrations.
- On VPS: `sudo ss -lntp | grep ':8765'`. If the bind address is not localhost, do not proceed; close public ingress and investigate.
- On Windows: if the client says port 8765 is occupied, close `START_AI_STUDIO.cmd` or another local listener and reconnect. If SSH closes, verify host/IP/key and `systemctl` status.
- For a code update: **stop the service**, update the reviewed checkout as the non-root owner, then start the service again. Never perform unreviewed migration or destroy the `~/.ai-automation-studio` store.
- Local SQLite, memory cards, task history and export files are still plaintext. **No backup service or automated backup restoration was installed**; establish independent encrypted snapshots and a restore drill before using important private business data.
- Security, compliance, tenant isolation, enterprise availability and actual Tencent Cloud behavior are **not established** by this repository. Systemd crash restart does not survive VPS suspension, cloud outages or an expired/terminated VM.

## What has and has not been verified

GitHub CI tests static unit properties, hostile input rejection, safe SSH argument construction, installer fail-closed behavior, and disposable HTTP quickstart from the prior release. A separate Linux job validates the generated unit with systemd tooling without installing the service or accessing any cloud account. This does NOT constitute successful deployment to the owner's Tencent VPS.

Required final live acceptance remains: verify VM `Running`; verify OS/Python; provision genuine owner password; install unit; read `is-active` and local `ss` output; connect via SSH tunnel from Windows; log into app; validate SQLite persists across an intentional service restart; optionally validate real VPS-side Ollama if available. Do not mark **ONLINE_24_7_VERIFIED** before those observations.

**Status at engineering handoff:** source implementation and CI verification tracked in `docs/PROGRESS.md`; Tencent instance access, SSH destination, live service and public-domain URL remain **UNVERIFIED / NOT DEPLOYED**.

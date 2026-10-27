# Deploying Portolan on Oracle Cloud (Always Free)

A checklist for the owner and for a Claude Code session helping them. It runs the same setup as
[deploy-lightsail.md](deploy-lightsail.md) (`deploy/lightsail/setup.sh`: the app container behind
Caddy with HTTPS and a login) on a free Oracle ARM server instead of AWS.

## Rules for the assistant

- **Never ask for, print, log or write the Gemini key.** The owner pastes it into `.env` on the
  server with `nano`. Don't `cat .env`; check only whether `GEMINI_API_KEY=` has a value.
- **No real LLM requests** beyond `portolan doctor` (which only lists models). Don't run
  `portolan record`, `portolan eval`, or paste papers into the app on the owner's behalf.
- **Ask before** pushing to GitHub, deleting anything in Oracle, or upgrading the Oracle account.
- Console clicks, the card check at sign-up, and pasting the key are the owner's to do. Tell them
  exactly what to click; don't guess at what the console shows, ask them to paste it.
- The owner is on Windows (PowerShell). Their SSH key is `C:\Users\ckamjith\.ssh\oracle_portolan`.

## Where things stand (2026-10-27)

| Step | State |
|---|---|
| Oracle account | Created (owner) |
| SSH key pair | Done: `~\.ssh\oracle_portolan` and `oracle_portolan.pub` |
| Deployment code | Committed locally on branch `deploy/lightsail` in `C:\Users\ckamjith\Downloads\Portolan`; **not pushed to GitHub yet** |
| Network (VCN) | **Not done.** The instance form's "create new network" option leaves *Assign public IPv4* greyed out; create the VCN with the wizard first (step 2) |
| Instance | Not created |
| Ports, install, login | Not done |

## Steps

### 1. Push the deployment branch (assistant, after the owner says yes)

The server installs from GitHub, so the branch must be there:

```bash
git -C C:\Users\ckamjith\Downloads\Portolan push -u origin deploy/lightsail
```

### 2. Create the network with the wizard (owner)

1. Oracle console → menu → **Networking** → **Virtual cloud networks**.
2. **Compartment** (left): the root compartment (the account name).
3. **Actions** → **Start VCN Wizard** → **Create VCN with Internet Connectivity** → **Start VCN Wizard**.
4. Name `portolan-vcn`, keep all defaults → **Next** → **Create**. Wait for all green ticks.

This makes a public subnet, an internet gateway and the routes.

### 3. Create the server (owner)

**Compute** → **Instances** → **Create instance**:

| Field | Value |
|---|---|
| Name | `portolan` |
| Image | Canonical Ubuntu 24.04 |
| Shape | Ampere → **VM.Standard.A1.Flex**, 2 OCPUs, 12 GB (free allowance: 4 OCPU / 24 GB total) |
| Primary network | Select existing → `portolan-vcn` |
| Subnet | Select existing → **public subnet-portolan-vcn** (not the private one) |
| Private IPv4 | Automatic (greyed-out options are fine) |
| Public IPv4 | **Automatically assign public IPv4 address: on** |
| SSH keys | **Paste public key**: the contents of `~\.ssh\oracle_portolan.pub` (`Get-Content $HOME\.ssh\oracle_portolan.pub`) |
| Storage | Defaults |

**Create**, wait for *Running*, note the **public IP**.

- *Out of capacity*: try another availability domain on the same form, or retry later.
- *Public IPv4 still locked*: create without it, then Instance → **Attached VNICs** → the VNIC →
  **IP administration / IPv4 addresses** → ⋮ → **Edit** → **Ephemeral public IP**.

### 4. Open ports 80 and 443 in Oracle's firewall (owner)

**Networking** → **Virtual cloud networks** → `portolan-vcn` → **Security** / **Security Lists** →
**Default Security List for portolan-vcn** → **Add Ingress Rules**, twice:

| Source CIDR | IP protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

Port 22 (SSH) is already open by default.

### 5. Connect (owner runs it; the assistant can't answer the prompts)

```bash
ssh -i $HOME\.ssh\oracle_portolan ubuntu@<PUBLIC_IP>
```

Type `yes` the first time. Everything below runs **on the server**.

### 6. Open the ports in Ubuntu's own firewall (server)

Oracle's Ubuntu images reject everything except SSH inside the server too:

```bash
sudo iptables -I INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT
sudo netfilter-persistent save
```

### 7. Install and start Portolan (server)

```bash
git clone -b deploy/lightsail https://github.com/chinmaykamjith001/Portolan.git
cd Portolan
bash deploy/lightsail/setup.sh
```

The script installs Docker (ARM builds; it picks the architecture itself), adds swap, creates
`.env` with `PORTOLAN_LLM_PROVIDER=gemini`, and stops to ask for the key. The owner:

```bash
nano .env
```

pastes the key after `GEMINI_API_KEY=` (right-click pastes in PowerShell), saves with **Ctrl+O**,
**Enter**, exits with **Ctrl+X**, and runs the script again:

```bash
bash deploy/lightsail/setup.sh
```

It asks for a site login name and password (12+ characters), builds (several minutes the first
time), runs `portolan doctor` (should end with "All good."), and prints the address, e.g.
`https://141-147-12-34.sslip.io`.

### 8. Check it works

- Open the printed address, log in, see the Portolan page. The first visit can take a minute
  while Caddy gets the HTTPS certificate.
- The owner pastes `2106.09685` (LoRA) to try it. That uses their free Gemini quota.

## Everyday commands (server, in `~/Portolan`)

| What | Command |
|---|---|
| Status | `sudo docker compose -f deploy/lightsail/compose.yml ps` |
| App log | `sudo docker compose -f deploy/lightsail/compose.yml logs --tail 100 portolan` |
| HTTPS / login log | `sudo docker compose -f deploy/lightsail/compose.yml logs --tail 100 caddy` |
| Update to the latest code | `bash deploy/lightsail/update.sh` |
| Check key and models | `sudo docker compose -f deploy/lightsail/compose.yml exec portolan portolan doctor` |
| Restart after editing `.env` / `portolan.toml` | `sudo docker compose -f deploy/lightsail/compose.yml up -d --force-recreate portolan` |
| Stop (data kept) | `sudo docker compose -f deploy/lightsail/compose.yml down` |
| Change the login | `rm deploy/lightsail/Caddyfile && bash deploy/lightsail/setup.sh` |

## Troubleshooting

| Symptom | Check |
|---|---|
| Address doesn't load / times out | Both ingress rules (step 4) exist on the **public** subnet's security list; step 6 was run (`sudo iptables -L INPUT -n --line-numbers` shows ACCEPT for 80,443 above any REJECT) |
| Certificate error | `logs caddy`; ports 80 and 443 must both be reachable from the internet |
| `git clone` says the branch doesn't exist | Step 1 (push) wasn't done |
| Build stops with "Killed" | Out of memory: `swapon --show`; or give the instance more memory (up to 24 GB free) |
| A page in the app says a step failed | `logs portolan`; the message names the reason (rejected key, used-up free quota) |
| Instance disappeared after a quiet week | Oracle reclaims idle Always Free instances; upgrading to Pay As You Go stops that (Always Free stays free; set a budget alert) |

## Not verified yet

The Docker image and `setup.sh` have not been run on a real server (no Docker or Linux was
available on the owner's machine). The first run here is the test; report any failure with the
exact output rather than working around it.

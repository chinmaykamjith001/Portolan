# Deploying Portolan on AWS Lightsail

One small Linux server runs the app container behind [Caddy](https://caddyserver.com), which adds
HTTPS and a login. Everything Portolan stores (database, cloned code, uploads) lives in a Docker
volume on that server. Expect about $12 a month for the 2 GB plan (check the price Lightsail shows).
That is well within $100 of AWS credits for six months.

Why not Lambda: Portolan builds a paper in background workers that keep running after a request
returns, streams progress over a long-lived connection, keeps its data in SQLite on local disk, and
runs `git` and sandboxed Python. Lambda freezes the process after each response and has no lasting
disk, so all of that would break.

You need: the AWS account, your Gemini key, and about 20 minutes. The commands below are typed in
the browser terminal that Lightsail opens for you; nothing has to be installed on your PC.

## 1. Set a billing alarm first

AWS console → **Billing and Cost Management** → **Budgets** → **Create budget** → *Use a template* →
**Monthly cost budget**. Set it to $20 and enter your email. You get an email if spending heads past
it, long before the credits run out.

## 2. Create the server

1. Open **Lightsail** (search "Lightsail" in the console) → **Create instance**.
2. Region: one near you. Platform: **Linux/Unix**. Blueprint: **OS Only** → **Ubuntu 24.04 LTS**.
3. Plan: the **2 GB** RAM plan. (1 GB works but builds slowly; more is not needed.)
4. Name it `portolan` → **Create instance**. Wait until it says *Running*.

## 3. Give it a fixed address and open HTTPS

1. Lightsail → **Networking** → **Create static IP** → attach it to `portolan`. Without this the
   address changes when the server restarts and HTTPS stops working. A static IP is free while it
   is attached; it is billed if you keep it after deleting the server.
2. Open the `portolan` instance → **Networking** tab → **IPv4 Firewall** → **Add rule** →
   application **HTTPS** (TCP 443) → **Create**. Leave SSH (22) and HTTP (80) as they are; Caddy
   needs port 80 to get its certificate and to redirect to HTTPS.

## 4. Install and start Portolan

On the instance page, click **Connect using SSH**. A terminal opens in your browser. Type:

```bash
git clone https://github.com/chinmaykamjith001/Portolan.git
cd Portolan
bash deploy/lightsail/setup.sh
```

(If the repository is private, GitHub asks for a username and password: use your GitHub name and a
[personal access token](https://github.com/settings/tokens) with read access, not your password.)

The script installs Docker, adds swap, and creates `.env`. It then stops and asks for your key:

```bash
nano .env
```

Paste your key right after `GEMINI_API_KEY=` (right-click → Paste in the browser terminal), save
with **Ctrl+O** then **Enter**, and exit with **Ctrl+X**. `PORTOLAN_LLM_PROVIDER` is already set to
`gemini`. Then run the script again:

```bash
bash deploy/lightsail/setup.sh
```

It asks for a login name and a password for the site (12+ characters; nothing is shown while you
type), builds the app (several minutes the first time), runs `portolan doctor`, and prints the
address, for example `https://3-91-20-7.sslip.io`. Open it, log in, and paste `2106.09685`.

The first visit can take a minute while Caddy gets the HTTPS certificate.

## Everyday commands

Run these in the server's terminal, inside the `Portolan` folder.

| What | Command |
|---|---|
| Is it running? | `sudo docker compose -f deploy/lightsail/compose.yml ps` |
| App log (errors) | `sudo docker compose -f deploy/lightsail/compose.yml logs --tail 100 portolan` |
| Get the latest code | `bash deploy/lightsail/update.sh` |
| Check the key and models | `sudo docker compose -f deploy/lightsail/compose.yml exec portolan portolan doctor` |
| Restart after editing `.env` or `portolan.toml` | `sudo docker compose -f deploy/lightsail/compose.yml up -d --force-recreate portolan` |
| Stop it (data is kept) | `sudo docker compose -f deploy/lightsail/compose.yml down` |
| Change the login | `rm deploy/lightsail/Caddyfile`, then `bash deploy/lightsail/setup.sh` |

It restarts by itself when the server reboots.

## Using your own domain

Point an `A` record at the static IP, then create the site with it instead of the sslip.io name:

```bash
rm deploy/lightsail/Caddyfile
PORTOLAN_DOMAIN=portolan.example.com bash deploy/lightsail/setup.sh
```

## Stopping the costs

Lightsail bills the instance while it exists, even when stopped. To end it: delete the instance,
then **Networking** → delete the static IP. The data goes with the instance; take a Lightsail
snapshot first if you want to keep it (snapshots are billed per GB per month).

## Good to know

- **The login is the only thing between the internet and your Gemini quota.** Use a strong password
  and don't share the address publicly.
- Free Gemini keys may let Google use what you send. Don't analyze private code with a free key.
- The key lives only in `.env` on the server (readable only by your user). It is not in the image,
  in git, or in the logs.
- On Linux the example sandbox gets the memory and file limits that Windows lacks.

## If something goes wrong

- **The address doesn't load or shows a certificate error:** check that the HTTPS (443) rule exists
  and the static IP is attached. If you attached the static IP after running the script, the site
  name still has the old address: `rm deploy/lightsail/Caddyfile`, then run `setup.sh` again.
  `sudo docker compose -f deploy/lightsail/compose.yml logs caddy` shows why a certificate failed.
- **The build stops with "Killed":** the server ran out of memory. Make sure swap is on
  (`swapon --show`), or pick a bigger plan.
- **A page says a step failed:** the app log (above) has the reason, such as a rejected key or a used-up
  free quota.

# Installation

Tweet Deleter runs one FastAPI process serving both the interface and API. It uses
SQLite and local files; there is no frontend build or separate database service.

Run it locally, through an SSH tunnel, or on your own HTTPS domain behind a reverse
proxy. Every installation belongs to one X account. A public address does not
create a multi-user service or let visitors access your posts.

## Requirements

| Requirement      | Details                                                                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Python           | 3.12 or newer, with `venv`, `pip` and SQLite support. Checks cover Python 3.12 and 3.15; not every intermediate version has been tested.         |
| Operating system | macOS or Linux. On Windows, use Ubuntu in WSL 2.                                                                                                 |
| Tools            | Git and `make`; SSH client/server for remote use.                                                                                                |
| Storage          | A private, writable directory on a local filesystem for SQLite, imports and backups. Allow space for the extracted archive and database backups. |
| Network          | Outbound HTTPS to X and its authorization service; package downloads during setup.                                                               |
| X account        | A Developer app with OAuth 2.0 user authentication and permission to delete posts. API credits are separate from X Premium.                      |
| Browser          | A current browser with JavaScript and cookies enabled.                                                                                           |

Node.js is needed only for development checks and browserless tests (24.15+).
MySQL, Redis, Celery, PHP, Docker and a web control panel are not required. No
measured minimum RAM or disk size has been established; archive size affects both.

Use one process per data directory. Do not enable multiple Uvicorn workers or
replicas, or store the live SQLite database on a network share.

## Ubuntu

These commands target a fresh Ubuntu 24.04 LTS installation, whose default Python
is 3.12. Other Ubuntu releases work when their selected interpreter satisfies the
requirement. See Ubuntu's [Python package](https://packages.ubuntu.com/noble/python3)
and [venv package](https://packages.ubuntu.com/noble/python3-venv) records.

Install system packages using an administrator account:

```sh
sudo apt update
sudo apt install git make python3 python3-venv
python3 --version
```

Then use a regular account to install the app:

```sh
git clone https://github.com/Ennygabby01/delete_tweets.git
cd delete_tweets
make setup
```

To choose another installed interpreter:

```sh
make setup PYTHON=python3.12
```

Do not replace Ubuntu's system Python or use `sudo pip`. Setup installs the locked
runtime dependencies into `.venv` and creates `.env` once, with a generated
credential-encryption key. Existing `.env` files are preserved.

Continue with [X configuration](#x-configuration), then [start the app](#start-and-stop).
For a remote machine, choose [HTTPS hosting](#public-https-hosting) or
[SSH access](#private-access-to-a-linux-server).

## macOS

Install Python 3.12+ from [python.org](https://www.python.org/downloads/macos/) or
your existing package manager. Install Apple's command-line tools if Git or `make`
is missing:

```sh
xcode-select --install
```

Run the clone and `make setup` commands above, then configure X. Rebuild `.venv`
after moving from another machine; copied interpreter links are not portable.

## Windows

Native PowerShell or Command Prompt execution is not supported. The application
uses Unix file locking through Python's [`fcntl`](https://docs.python.org/3/library/fcntl.html)
and the Makefile uses POSIX shell commands. Installing `make` alone does not fix this.

Install Ubuntu in WSL 2 from an administrator PowerShell:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart if prompted and create your Linux user. See Microsoft's
[WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install)
for prerequisites and existing installations.

Inside the Ubuntu terminal, follow the Ubuntu instructions. Clone under your Linux
home directory, such as `~/delete_tweets`, and keep the database there. Use the
Windows browser to open the printed `http://127.0.0.1:PORT` address. WSL normally
forwards Linux applications to Windows localhost; see Microsoft's
[networking guide](https://learn.microsoft.com/en-us/windows/wsl/networking) if it fails.
Keep the loopback address rather than exposing the service on a LAN address.

## X configuration

1. Open the [X Developer Console](https://console.x.com/) and select or create your app.
2. In its user authentication settings, enable OAuth 2.0. For this guide, select a
   confidential **Web App / Automated App / Bot** client and enable write access
   where offered.
3. Put its **OAuth 2.0 Client ID** and **Client Secret** into the generated `.env`:

   ```dotenv
   X_CLIENT_ID=your-client-id
   X_CLIENT_SECRET=your-client-secret
   ```

4. Start the app and register the exact callback printed in the terminal. The
   default is `http://127.0.0.1:8000/v1/auth/callback`.
5. Open the printed app address and sign in. Authorization returns to the current
   tab. Locally, the first verified X account owns the installation. Public HTTPS
   deployments also require `X_OWNER_ID` before startup.

Keep `APP_ENCRYPTION_KEY` unchanged. Consumer/API keys and the app-only Bearer Token
are not substitutes for these OAuth 2.0 credentials. The redirect URI must match
what is registered; see [X's authorization-code documentation](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code).

Check billing and access in Developer Console before deletion. Reads are off by
default; **Settings → X connection → Allow X post reads** enables manual sync.
Enabling reads does not fetch posts immediately. Local archive deletion sends saved
IDs directly without a post-read preflight. Deletion requests can still cost credits.

## Start and stop

From the project directory:

```sh
make start
```

The terminal prints the app URL and callback. If port 8000 is occupied, the launcher
reserves another available loopback port. Use the printed address and register its
exact callback before signing in.

```sh
make start PORT=8001
```

Locally, this selects a preferred port and can change if occupied. HTTPS hosting
uses a fixed backend port and stops on a conflict instead. Press **Ctrl+C** to
stop. `make dev` enables Python reload for development; use `make start` for normal use.
Changes to `.env` require a restart. `make dev` is rejected for HTTPS hosting. All configuration options are listed in
[.env.example](.env.example). `DATA_DIR`, when set, should be an absolute private path.

## Public HTTPS hosting

Use a dedicated domain or subdomain, such as `delete.example.com`, with its DNS
pointing to your server. Serve the app at the domain root, not a URL subdirectory.
Install the app as an unprivileged user outside the web document root and run one
process through the [systemd example](#optional-systemd-service).

### Environment and owner

Edit the generated `.env` on the server:

```dotenv
APP_ORIGIN=https://delete.example.com
APP_BIND_HOST=127.0.0.1
APP_BIND_PORT=8000
TRUSTED_PROXY_IPS=127.0.0.1
X_OWNER_ID=your-numeric-x-account-id
```

Replace the domain and owner ID with your values. `X_OWNER_ID` is your numeric
account ID, not your handle, OAuth client ID or app ID. You can find `accountId` in
an extracted X archive's `data/account.js`, or `profile.id` in the `/v1/session`
response of your own already signed-in local installation. Do not use a third-party
ID lookup service or share the session response, which also contains your CSRF token.

Only that X account may connect. If an existing database belongs to another owner,
startup fails; changing `.env` is not an ownership transfer. Keep your generated
`APP_ENCRYPTION_KEY` and OAuth client credentials when migrating an installation.

| Setting             | Purpose                                                                                                  |
| ------------------- | -------------------------------------------------------------------------------------------------------- |
| `APP_ORIGIN`        | Browser-facing origin and OAuth callback base. Use HTTPS for a domain, without a path.                   |
| `APP_BIND_HOST`     | Backend IPv4 loopback address; defaults to `127.0.0.1`. The public proxy connects here.                  |
| `APP_BIND_PORT`     | Backend port; defaults to 8000 in HTTPS mode. Must match the proxy upstream and service.                 |
| `TRUSTED_PROXY_IPS` | Exact connecting proxy IPs, comma-separated. Required for HTTPS; wildcards and CIDR ranges are rejected. |
| `X_OWNER_ID`        | Numeric allowed X account ID. Required for HTTPS, optional locally.                                      |
| `MAX_CONNECTIONS`   | Uvicorn concurrent-connection limit, default 64.                                                         |

The public origin is independent of the backend port. Starting on port 8000 still
uses `https://delete.example.com/v1/auth/callback`. Register that exact HTTPS
callback in X, and update the app's website URL there. HTTPS mode sets Secure,
HttpOnly, host-only session cookies. Plain HTTP requests to the backend fail unless
an explicitly trusted proxy reports HTTPS. The proxy must replace client-supplied
forwarding headers. The app never uses a forwarded host to choose its callback.

### Ubuntu with nginx

Install nginx if it is not already managed by a hosting panel:

```sh
sudo apt install nginx
```

Create the domain's nginx site and obtain a valid certificate using your existing
certificate process or [Certbot's nginx instructions](https://certbot.eff.org/instructions?os=snap&ws=nginx).
The following is the final configuration **after the certificate exists**. Replace
all domain and certificate paths. If your certificate tool manages an HTTP ACME
challenge location, preserve that location when adding the redirect.

```nginx
server {
    listen 80;
    server_name delete.example.com;
    return 308 https://delete.example.com$request_uri;
}

server {
    listen 443 ssl;
    server_name delete.example.com;
    ssl_certificate /etc/letsencrypt/live/delete.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/delete.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    access_log off;
    client_max_body_size 64m;
    client_header_timeout 15s;
    client_body_timeout 30s;
    large_client_header_buffers 4 4k;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host delete.example.com;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Host "";
        proxy_set_header Forwarded "";
        proxy_set_header Connection "";
        proxy_connect_timeout 5s;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
        proxy_cache off;
        proxy_next_upstream off;
    }
}
```

Reject unknown domains in your nginx default virtual host rather than sending them
to this app. Only open the required SSH/HTTP/HTTPS firewall ports; keep the backend
loopback-only. If `MAX_ARCHIVE_BYTES` changes, adjust `client_max_body_size` as well.
Disable cache/CDN rules for the entire app. Do not automatically retry proxied
requests, including OAuth and deletion confirmations. Avoid access logs containing
OAuth callback queries; review any panel, CDN and error logs for the same exposure.

For a standalone Ubuntu nginx site, save it under `/etc/nginx/sites-available/`,
enable it from `sites-enabled`, then validate before reloading:

```sh
sudo nginx -t
sudo systemctl reload nginx
```

Use the domain in your browser and confirm that sign-in returns there. There is no
need to open the backend port publicly. Review the
[nginx proxy-header documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header)
and [FastAPI proxy guidance](https://fastapi.tiangolo.com/advanced/behind-a-proxy/)
when adapting this for an existing edge. This guide covers one same-host TLS proxy;
additional proxies need their own explicit trust and header-replacement design.

## Private access to a Linux server

Install and start the app on the server as above. Use an unprivileged account and
SSH key authentication. If SSH is not already installed, follow Ubuntu's
[OpenSSH server guide](https://ubuntu.com/server/docs/how-to/security/openssh-server/).
Only SSH needs inbound access; keep the application port closed in the host,
cloud and panel firewalls.

On your own computer, forward the server's selected app port. Replace `8000` on
both sides if the launcher printed another port, and replace `user@server`:

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8000:127.0.0.1:8000 user@server
```

Keep this terminal open and browse to `http://127.0.0.1:8000`. X returns to that
same local address through the tunnel. The browser port, server port, app origin
and registered callback must agree. If the local port is busy, choose a port free
on both machines and restart the server with that preferred port. Confirm what
it actually selects before reconnecting the tunnel.

SSH forwarding must be allowed for your account. `ExitOnForwardFailure` detects a
local forwarding setup failure; it does not prove the app is running at the remote
end. See [OpenSSH's port-forwarding options](https://man.openbsd.org/ssh.1#L).

On a server, archives, tokens and history live on that server. SSH encrypts the
connection, but the hosting provider and server administrator remain within your
trust boundary. This is still a single-account installation, not a shared service.

### Optional systemd service

For a fresh Ubuntu server, use a dedicated regular account named `tweetdeleter`
and install the checkout at `/home/tweetdeleter/delete_tweets`. Complete setup and
edit `.env` as that account first. These are example paths, not existing service names.

Create `/etc/systemd/system/tweet-deleter.service` as administrator:

```ini
[Unit]
Description=Tweet Deleter
After=network.target

[Service]
Type=simple
User=tweetdeleter
Group=tweetdeleter
WorkingDirectory=/home/tweetdeleter/delete_tweets
ExecStart=/home/tweetdeleter/delete_tweets/.venv/bin/python scripts/serve.py
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Adjust user, group, working directory and executable together if you chose another
location. Do not run a second foreground copy against the same data directory.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now tweet-deleter.service
sudo systemctl status tweet-deleter.service
sudo journalctl -u tweet-deleter.service -n 50 --no-pager
```

The launcher reads configuration from `.env`; systemd environment values take
precedence if also set. Read the journal for the URL/callback before connecting. A
service restart pauses interrupted runs; it does not automatically resume deletion.
Use `sudo systemctl stop tweet-deleter.service` before backups or maintenance.

### aaPanel

Use the Linux setup and systemd service above on a VPS you administer. aaPanel's
usual `/www/wwwroot/<site>` layout is a website document root: keep this checkout,
`.env`, data and backups outside every public document root. The dedicated home
path in the service example provides that separation.

If using aaPanel's **Python Environment → Version management**, select Python 3.12+
and pass its actual interpreter path as `PYTHON` to `make setup`. Do not reuse a
virtual environment from another project or machine. aaPanel's own Python runtime
is not the application's runtime. See its
[Python Project documentation](https://www.aapanel.com/docs/Function/python.html).

For public access on an nginx-based aaPanel server:

1. Add the domain as a website with an empty, separate document root. Do not upload
   the source or private data into it.
2. Use the site's SSL settings to issue/install its certificate and enable HTTPS.
3. Set the app's `.env` as described under [HTTPS hosting](#public-https-hosting).
4. Configure a reverse proxy from `/` to `http://127.0.0.1:8000`, preserving the
   domain as Host. Apply the header replacement, timeout, upload-limit, no-cache
   and no-retry directives from the nginx example inside that site's existing
   configuration. Replace a generated `location /` rather than adding a duplicate.
5. Disable request logging that records callback queries. Check the generated
   configuration for competing cache/static-file locations that bypass the proxy.
6. Validate with aaPanel's nginx executable/configuration and reload through the
   panel, then register the HTTPS callback in X.

Keep aaPanel's existing SSL paths, validation locations and managed includes.
Do not paste the standalone server blocks over a panel-managed site. Consult
[aaPanel's website controls](https://www.aapanel.com/docs/SubaaPanel/Website.html)
for configuration and reverse-proxy settings; names vary by panel edition.

Systemd is the only app process manager in this guide. Do not also launch it through
Python Project, Supervisor or a scheduled task. For SSH-only access, no public
website/proxy configuration is needed.

### cPanel

cPanel's standard Application Manager documents Python **WSGI** under Passenger.
This app requires a continuously running **ASGI** process with its internal run
worker. A `passenger_wsgi.py` entry point is not sufficient. See
[cPanel's Python guide](https://docs.cpanel.net/knowledge-base/web-services/how-to-install-a-python-wsgi-application/).

On shared hosting, ask the provider whether it permits Python 3.12+, long-running
ASGI processes, private local SQLite storage and an HTTPS reverse proxy to that
process. For SSH-only use, SSH forwarding is needed instead. If unavailable, run
locally or use a VPS. A Python selector alone does not establish compatibility.

On a cPanel VPS with administrator access:

1. Install/run the app under a dedicated unprivileged account, outside `public_html`.
   Use the systemd pattern above and your operating system's package manager;
   Ubuntu `apt` instructions do not apply to AlmaLinux or Rocky Linux.
2. Configure a dedicated domain with a valid certificate through cPanel/AutoSSL.
3. Set the HTTPS app environment and owner ID, with backend port 8000 on loopback.
4. Have the administrator configure that domain's **SSL virtual host** as a reverse
   proxy using Apache's `mod_proxy`, `mod_proxy_http` and `mod_headers`.

A starting point for the SSL virtual-host include is:

```apache
ProxyRequests Off
ProxyPreserveHost On
RequestHeader set Host "delete.example.com"
RequestHeader set X-Forwarded-Proto "https"
RequestHeader set X-Forwarded-For "expr=%{REMOTE_ADDR}"
RequestHeader unset X-Forwarded-Host
RequestHeader unset Forwarded
ProxyAddHeaders Off
ProxyPass "/" "http://127.0.0.1:8000/" connectiontimeout=5 timeout=60 retry=0
ProxyPassReverse "/" "http://127.0.0.1:8000/"
LimitRequestBody 67108864
```

Replace the domain. Store overrides using
[cPanel's per-domain include mechanism](https://docs.cpanel.net/ea4/apache/modify-apache-virtual-hosts-with-include-files/),
such as `/etc/apache2/conf.d/userdata/ssl/2_4/USER/DOMAIN/tweet-deleter.conf`;
do not edit generated `httpd.conf`. Follow cPanel's rebuild/validation procedure
before restarting Apache. Redirect HTTP to HTTPS, disable caching and automatic
request replay, and arrange domain-specific access logging that omits OAuth query
strings. The administrator must reconcile other modules/includes and request
timeouts. See Apache's [proxy](https://httpd.apache.org/docs/2.4/mod/mod_proxy.html)
and [request-header](https://httpd.apache.org/docs/2.4/mod/mod_headers.html) documentation.

Do not install a competing nginx listener over cPanel's existing web server. If you
do not have administrator access, the provider must configure the proxy for you.

## Updates, backups and recovery

1. Stop the app or its service. Wait for shutdown before copying data.
2. Privately back up the **entire data directory and matching `.env`**, together
   with any original archive files you need. Do not publish them in the repository.
3. Update to the intended source version and run `make setup` as the installation
   account. Do not discard unrelated local changes or replace the encryption key.
4. Start the app, confirm its printed port/callback, and inspect Activity before
   resuming any paused run.

The default data directory is `data/` beside the source. Use the configured
`DATA_DIR` if overridden. Backups contain private post text and account credentials;
protect and encrypt them separately. Credential encryption does not encrypt the
whole database. Losing `.env`'s key makes saved encrypted tokens unreadable.

To move machines, restore the stopped data directory and matching `.env`, then
create a fresh `.venv` with `make setup`. Never merge SQLite files or copy a live
main database without its journal state. Restore a complete stopped backup into a
clean destination; do not leave unrelated WAL/SHM files beside it.

For v1 migration, preserve root `tweets.js`, `filters.json` and `credentials.json`.
Startup imports the archive and filters; browser sign-in is required again. Schema-v1
and schema-v2 SQLite upgrades create a backup under `data/backups/` first. Current
schema v3 stores scheduled runner pacing; older application builds reject it rather
than silently ignoring that pacing. A source rollback alone
may not reverse a database migration; keep a compatible code/config/data backup.
Restoring old history cannot undo deletions already completed on X. Review uncertain
outcomes before retrying anything.

## Troubleshooting

| Symptom                                  | Check                                                                                                                         |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Existing `.venv` is broken or too old    | Stop the app, move `.venv` aside and rerun setup with a working Python 3.12+ interpreter. Keep `.env` and data.               |
| `venv` or `ensurepip` is missing         | Install the matching interpreter's venv package, such as `python3-venv` on Ubuntu 24.04.                                      |
| Sign-in is disabled after editing `.env` | Restart the app and check that the OAuth 2.0 client values are set in this checkout's `.env`.                                 |
| Callback rejected or wrong address       | Compare the actual printed callback, X registration and browser URL, including the port.                                      |
| Invalid origin/host                      | Use the configured origin. For HTTPS, check the proxy Host and X-Forwarded-Proto replacement and exact trusted connecting IP. |
| Database already in use                  | Stop the other app process. Do not remove the lock file to bypass a running process.                                          |
| Sync is disabled                         | Enable post reads in Settings. Sync can also be blocked by an active run, an in-flight request or read backoff.               |
| Billing/access failure                   | Check X credits and app permissions. Changing clients does not bypass charges.                                                |
| Uncertain result after interruption      | Keep it protected until explicit reconciliation; enabling reads may be needed to check it.                                    |

## Development and verification

```sh
make setup-dev
make check
make test
make audit
```

Install Node.js 24.15+ for frontend checks and tests. X responses are mocked and
SQLite tests use temporary databases; these commands do not delete account posts.

The platform instructions are based on the current source and linked official
documentation, checked on 25 November 2026. They have not been exercised as fresh
Ubuntu, aaPanel, cPanel or WSL installations. Automated tests cover HTTPS OAuth,
cookie flags, host/origin rejection, owner binding and trusted-proxy behavior;
they do not verify a particular live TLS/proxy deployment.

### Runner configuration

`MAX_SCHEDULE_POSTS` bounds the total posts in one runner (default 5000, maximum
10000). `MAX_POSTS_PER_RUN` also bounds the runner's batch size. X's observed
allowance can reduce a batch further; `X_DELETE_WINDOW_SECONDS` is the fallback
cooldown when no valid reset header is available. These settings do not buy API
credits or change X's limits. The app performs no balance requests.

Keep one worker process per data directory. A confirmed runner can continue when
the browser closes, but application restarts pause it. Restore compatible source,
configuration and database backups together when rolling back a schema upgrade.

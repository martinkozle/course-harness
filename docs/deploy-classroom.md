# Deploy Course Harness Classroom

Course Harness Classroom serves published Course Releases to Learners and Instructors. It runs as
one container from this repository, behind a reverse proxy that terminates TLS. This guide covers
building and running it with Docker Compose, the reverse proxy, backups, upgrades, and the path
from a Course in Course Harness to a link you share.

## What runs

`packages/classroom/Dockerfile` builds the image from the repository root in three stages:

1. Bun builds the Classroom interface (`frontend/classroom`) from `bun.lock`.
2. uv installs the Classroom and its locked dependencies from `uv.lock` into `/opt/classroom`
   (`uv sync --frozen --no-dev --package course-harness-classroom`), then copies only
   `course_harness/__init__.py` and `course_harness/core/` from Course Harness next to it. Course
   Harness itself, docling and the agent stack are never installed (ADR 0017).
3. The runtime is `python:3.14-slim` (Debian trixie) with Git, headless LibreOffice Impress and
   metric-compatible fonts, running as the non-root user `classroom` (uid 10001). The interface
   is at `/opt/classroom-static` (`CLASSROOM_STATIC_DIR`), and all state lives in the `/data`
   volume (`CLASSROOM_DATA_DIR`): the SQLite store, the bare mirrors, the served Releases and
   their rendered slide backgrounds.

The build runs `packages/classroom/docker/check_image.py`, which fails the build if docling,
pydantic-ai, fastmcp or any `course_harness` module outside `core` is importable, imports
`course_harness.core` and the Classroom app, and renders one slide with LibreOffice. You can run
it again in a running container:

```bash
docker compose exec classroom python -I /opt/check_image.py --render
```

The container listens on port 8000 and has a health check on `/healthz`. The image is about
950 MB on disk (about 250 MB compressed), most of it LibreOffice.

## Build and run

You need Docker with Compose v2 (rootless Docker works). From a checkout of this repository:

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'   # paste as CLASSROOM_ADMIN_TOKEN
$EDITOR .env                                                    # also set CLASSROOM_PUBLIC_URL
docker compose up -d --build
docker compose ps                                               # wait for "healthy"
```

`compose.yaml` runs the `classroom` service with:

- the named volume `classroom-data` at `/data`;
- the port bound to `127.0.0.1:8000` only (`CLASSROOM_PORT` changes the host port), so only the
  reverse proxy on the same host reaches it;
- `restart: unless-stopped`, a read-only root filesystem with a 512 MB `tmpfs` at `/tmp` for
  LibreOffice and Git, all capabilities dropped, `no-new-privileges`, and memory and process
  limits.

The settings come from `.env` (see `.env.example`); `CONTRIBUTING.md` lists every
`CLASSROOM_*` variable.

| Variable | In production |
| --- | --- |
| `CLASSROOM_ADMIN_TOKEN` | Required: the Administrator token for `/admin`, at least 24 characters. Keep it in `.env`, never in a URL. |
| `CLASSROOM_PUBLIC_URL` | Required: the `https://` address people use, such as `https://classroom.example.org`. Share Links use it, and cookies are `Secure`. |
| `CLASSROOM_TRUSTED_PROXIES` | The address the reverse proxy connects from, as the container sees it. |
| `CLASSROOM_RENDER_SANDBOX` | `auto` (default) or `required`; see [LibreOffice and untrusted templates](#libreoffice-and-untrusted-templates). |

Never set `CLASSROOM_ALLOW_LOCAL_REPOS` in production: it lets the Classroom fetch from local
paths and private network addresses.

### CLASSROOM_TRUSTED_PROXIES

The Classroom limits requests per client address, for example 240 answers a minute and a limit on
opening Share Links. Behind a proxy every request comes from the proxy's address, so the Classroom
reads the real client address and scheme from `X-Forwarded-For` and `X-Forwarded-Proto`, but only
from the addresses in `CLASSROOM_TRUSTED_PROXIES` (comma-separated addresses or networks). Unset,
the headers are ignored and all Learners share one rate limit.

A proxy on the host connects through the Docker bridge gateway (such as `172.18.0.1`), so the
compose default trusts Docker's bridge networks, `172.16.0.0/12`. If your proxy runs in another
container, set the proxy container's address instead. Only the proxy should be able to reach the
container: keep the port bound to `127.0.0.1`, and do not attach other containers to the
Classroom's network, because anything on a trusted address can claim any client address. To
check, open the Classroom through the proxy and look at `docker compose logs classroom`: requests
should show your own address, not the gateway's.

## Reverse proxy

The proxy terminates TLS, sends HSTS, forwards the client address, and keeps Share Link tokens out
of its access log. Learner links are `/l/<token>` and Instructor links `/i/<token>`; the
Classroom exchanges the token for an HttpOnly cookie and redirects to a clean `/c/<course>`
address, and its own log already shows `/l/[redacted]`. Your proxy's access log must redact them
too, or anyone who can read the log can open the course.

Attaching or syncing a course fetches the repository during the request, which can take up to
`CLASSROOM_FETCH_TIMEOUT` (300 seconds by default), so allow long responses on `/api/admin/`.

### Caddy

Caddy obtains and renews the certificate itself.

```caddy
classroom.example.org {
	encode zstd gzip
	header Strict-Transport-Security "max-age=63072000; includeSubDomains"

	reverse_proxy 127.0.0.1:8000

	log {
		output file /var/log/caddy/classroom.access.log
		format filter {
			# /l/<token> and /i/<token> are Share Links: never log the token.
			request>uri regexp ^/([li])/[^/?#]+ /${1}/[redacted]
		}
	}
}
```

Caddy sets `X-Forwarded-For` and `X-Forwarded-Proto` itself and does not log cookies by default.

### nginx

```nginx
# In the http block: log Share Link paths without their token.
map $request_uri $classroom_log_uri {
    ~^/(?<role>[li])/[^/?#]+(?<rest>.*)$ /$role/[redacted]$rest;
    default $request_uri;
}
log_format classroom '$remote_addr - $remote_user [$time_local] '
                     '"$request_method $classroom_log_uri $server_protocol" '
                     '$status $body_bytes_sent "$http_user_agent"';

server {
    listen 80;
    server_name classroom.example.org;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name classroom.example.org;

    ssl_certificate     /etc/letsencrypt/live/classroom.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/classroom.example.org/privkey.pem;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

    access_log /var/log/nginx/classroom.access.log classroom;
    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        # Replace, never append to, what the client sent.
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/admin/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
    }
}
```

The `classroom` log format never logs `$request` or `$request_uri`, which hold the token, and
leaves out the `Referer` header. nginx's error log still includes the request line when a request
fails (for example while the Classroom restarts), so restrict who can read it.

## LibreOffice and untrusted templates

When the Classroom syncs a Release, it renders the slide backgrounds of the Release's PowerPoint
template with LibreOffice. The template comes from a repository someone else controls, so the
image treats the rendering as untrusted:

- `/usr/local/bin/libreoffice` wraps `soffice` and runs it in a new user and network namespace
  (`unshare --user --map-root-user --net`) that has no network interface but loopback, so a
  template cannot fetch anything or reach the network.
- Docker's default seccomp profile refuses unprivileged user namespaces, so with the default
  `compose.yaml` the sandbox cannot be created. With `CLASSROOM_RENDER_SANDBOX=auto` (the
  default) LibreOffice then runs with the container's network; with `required` it does not run
  at all, and the Classroom shows plain slide backgrounds with the same copyable text.
- In both cases `classroom-hardening.xcd` sets finalized LibreOffice defaults: no macros (highest
  macro security, macro execution disabled), no active content (OLE, DDE, OLE automation), links
  from the document blocked (`BlockUntrustedRefererLinks`), and no Java. The image has no
  LibreOffice update checker. Each render uses a throwaway profile, runs as the unprivileged
  `classroom` user on a read-only root filesystem with no capabilities, is limited to 30
  seconds, after `course_harness.core` has checked the template's zip size and entry count.

The residual risk with the default compose file: a malicious template that exploits a LibreOffice
bug could make network requests from the container (the same network the Classroom uses to fetch
from GitHub) and read or change what the `classroom` user can: `/data`, including the SQLite
store with Learner answers. To close the network part, run the container where unprivileged user
namespaces are allowed, for example with a seccomp profile derived from Docker's default that also
permits `unshare` and `clone` with `CLONE_NEWUSER | CLONE_NEWNET` (passed with
`security_opt: [seccomp=/path/to/profile.json]`), and set `CLASSROOM_RENDER_SANDBOX=required`, so
rendering never falls back to running with the network. `docker compose exec classroom
python -I /opt/check_image.py --render` then confirms the sandbox works. Attach only repositories
from Course Authors you trust, and keep the image up to date for LibreOffice security fixes.

## Backups

The `classroom-data` volume holds everything. The SQLite store (`classroom.sqlite3`) is the part
you cannot get back: attached courses, Share Link hashes, sessions, Learner identities and
answers. Mirrors, served Releases and rendered backgrounds are rebuilt from the repositories on the
next sync.

A consistent online copy of the store, with SQLite's backup API:

```bash
docker compose exec classroom python -I -c "import sqlite3; \
src = sqlite3.connect('/data/classroom.sqlite3'); dst = sqlite3.connect('/tmp/backup.sqlite3'); \
src.backup(dst); dst.close()"
docker compose cp classroom:/tmp/backup.sqlite3 "./classroom-$(date +%F).sqlite3"
```

Or the whole volume, with the service stopped for a moment:

```bash
docker compose stop classroom
docker run --rm -v course-harness-classroom_classroom-data:/data:ro -v "$PWD":/backup \
  debian:trixie-slim tar -C /data -czf "/backup/classroom-data-$(date +%F).tar.gz" .
docker compose start classroom
```

To restore, stop the service, empty the volume, extract the archive into it (as above, with
`tar -xzf`), make sure the files belong to uid 10001, and start it again. The backups contain
Learner names, student IDs and answers: store them as carefully as the server.

## Upgrade

```bash
git pull
docker compose up -d --build
docker image prune
```

The Classroom migrates its SQLite store on start, so back it up first. Rebuilding also picks up
Debian security updates for LibreOffice and Git; rebuild without the cache now and then
(`docker compose build --pull --no-cache`) to get them.

## From Course Harness to a shared link

1. **Publish** a Course Release in Course Harness (**Publish a Course Release**). A Release is a
   Git tag `course-release/<slug>` with a manifest, plus a ref holding its PowerPoint files.
2. **Push** the Course Workspace from the Release canvas to its remote, a public GitHub
   repository. Push sends `main`, every Release tag and the Release files refs (see "Course
   Workspace remotes" in `CONTRIBUTING.md`).
3. **Attach** the repository in the Classroom: open `https://classroom.example.org/admin`, sign
   in with the Administrator token, and enter the repository's HTTPS address, such as
   `https://github.com/owner/course`, under **Attach a course**. The Classroom mirrors the
   repository, validates the newest Release and serves it. It syncs every
   `CLASSROOM_FETCH_INTERVAL` seconds, and **Sync now** syncs at once; a new Release that is not
   valid is skipped and the previous one stays.
4. **Share** links: **Create Learner link** and **Create Instructor link** show each link once.
   Send the Learner link to the students and the Instructor link to the teaching staff. Anyone
   with a link can open the course, so share it only with them; **Rotate** a link to stop the old
   one working.

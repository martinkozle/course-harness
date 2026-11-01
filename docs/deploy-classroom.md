# Deploy Course Harness Classroom

Course Harness Classroom serves published Course Releases to Learners and Instructors. It runs as
one container from this repository, behind a reverse proxy that terminates TLS. This guide covers
building and running it with Docker Compose, the reverse proxy, backups, upgrades, and the path
from a Course in Course Harness to a link you share.

## What runs

`packages/classroom/Dockerfile` builds the image from the repository root in three stages. Each
base image is pinned by digest, with its tag in a comment:

1. `oven/bun:1.3.13` builds the Classroom interface (`frontend/classroom`) from `bun.lock`.
2. `python:3.14.6-slim-trixie` with uv 0.11.28 installs the Classroom's locked dependencies from
   `uv.lock` into `/opt/classroom` (`uv sync --frozen --no-dev --package
   course-harness-classroom`, dependencies before the source so they stay cached), then copies
   only `course_harness/__init__.py` and `course_harness/core/` from Course Harness next to it.
   Course Harness itself, docling and the agent stack are never installed (ADR 0017).
3. The runtime is `python:3.14.6-slim-trixie` (Debian 13) with Git, headless LibreOffice Impress
   (`libreoffice-impress-nogui`) and metric-compatible fonts, running as the non-root user
   `classroom` (uid and gid 10001). The interface is at `/opt/classroom-static`
   (`CLASSROOM_STATIC_DIR`), and all state lives in the `/data` volume (`CLASSROOM_DATA_DIR`): the
   SQLite store, the bare mirrors, the served Releases and their rendered slide backgrounds.

The build runs `packages/classroom/docker/check_image.py`, which fails the build if docling,
pydantic-ai, fastmcp or any `course_harness` module outside `core` is importable, imports
`course_harness.core` and the Classroom app, and renders one slide with LibreOffice. You can run
it again in a running container; it also says whether the LibreOffice sandbox is active:

```bash
docker compose exec classroom python -I /opt/check_image.py --render
```

The container listens on port 8000 and has a health check on `/healthz` (left out of the access
log). The image is about 950 MB on disk (about 250 MB compressed), most of it LibreOffice.

## Build and run

You need Docker with Compose 2.24 or later (rootless Docker works). From a checkout of this
repository:

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'   # paste as CLASSROOM_ADMIN_TOKEN
$EDITOR .env                                                    # also set CLASSROOM_PUBLIC_URL
docker compose up -d --build
docker compose ps                                               # wait for "healthy"
docker compose logs classroom | grep LibreOffice                # the sandbox is active
```

`compose.yaml` runs the `classroom` service with:

- the named volume `classroom-data` at `/data`;
- the port bound to `127.0.0.1:8000` only (`CLASSROOM_PORT` changes the host port), so only the
  reverse proxy on the same host reaches it;
- its own network with the fixed subnet `172.29.81.0/24` and gateway `172.29.81.1`;
- `restart: unless-stopped` and `init: true`, so an init process reaps LibreOffice's children;
- a read-only root filesystem with a 512 MB `tmpfs` at `/tmp` for LibreOffice and Git, all
  capabilities dropped, `no-new-privileges`, the seccomp profile
  `packages/classroom/docker/seccomp-classroom.json` (see
  [LibreOffice and untrusted templates](#libreoffice-and-untrusted-templates)), and memory and
  process limits.

Every variable in `.env` reaches the Classroom (`env_file`); `CONTRIBUTING.md` lists all
`CLASSROOM_*` settings. These matter in production:

| Variable | In production |
| --- | --- |
| `CLASSROOM_ADMIN_TOKEN` | Required: the Administrator token for `/admin`, at least 24 characters. Keep it in `.env`, never in a URL. |
| `CLASSROOM_PUBLIC_URL` | Required: the `https://` address people use, such as `https://classroom.example.org`. Share Links use it, and cookies are `Secure`. Set it so the links shown on `/admin` and in Insights use the public host; without it they use whatever host the request came in on, which behind a proxy may be an internal one. |
| `CLASSROOM_TRUSTED_PROXIES` | The address the reverse proxy connects from, as the container sees it. Compose default: `172.29.81.1`. |
| `CLASSROOM_RENDER_SANDBOX` | `required` (compose default) or `auto`; see [LibreOffice and untrusted templates](#libreoffice-and-untrusted-templates). |

Never set `CLASSROOM_ALLOW_LOCAL_REPOS` in production: it lets the Classroom fetch from local
paths and private network addresses.

### CLASSROOM_TRUSTED_PROXIES

The Classroom limits requests per client address, for example 240 answers a minute and a limit on
opening Share Links. Behind a proxy every request comes from the proxy's address, so the Classroom
reads the real client address and scheme from `X-Forwarded-For` and `X-Forwarded-Proto`, but only
from the addresses in `CLASSROOM_TRUSTED_PROXIES` (comma-separated addresses or networks). Unset,
the headers are ignored and all Learners share one rate limit.

A proxy on the host connects to the published port, and the container sees the connection come
from the gateway of its network. `compose.yaml` pins that network to `172.29.81.0/24` with the
gateway `172.29.81.1` and trusts only the gateway. If the subnet collides with one of your
networks, change the subnet, the gateway and `CLASSROOM_TRUSTED_PROXIES` together. If your proxy
runs in a container instead, attach it to the Classroom's network with a fixed address and trust
that address.

Anything that connects from a trusted address can claim any client address, so only the proxy may
reach the container: keep the port bound to `127.0.0.1`, and attach no other containers to the
Classroom's network. To check, open the Classroom through the proxy and look at
`docker compose logs classroom`: requests should show your own address, not `172.29.81.1`.

## Reverse proxy

The proxy terminates TLS, sends HSTS, forwards the client address, and keeps Share Link tokens out
of its access log. Learner links are `/l/<token>` and Instructor links `/i/<token>`; the
Classroom exchanges the token for an HttpOnly cookie and redirects to a clean `/c/<course>`
address, and its own log already shows `/l/[redacted]`. Your proxy's access log must redact them
too, or anyone who can read the log can open the course.

Attaching or syncing a course fetches the repository during the request, which can take up to
`CLASSROOM_FETCH_TIMEOUT` (300 seconds by default), so allow long responses on `/api/admin/`.

### Caddy

Caddy obtains and renews the certificate itself and redirects HTTP to HTTPS.

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
    # A Share Link opened over plain HTTP still carries its token: redact it here too.
    access_log /var/log/nginx/classroom.access.log classroom;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
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

`listen 443 ssl http2` works on every current nginx; from 1.25.1 you may write `listen 443 ssl`
with `http2 on;` instead. The `classroom` log format never logs `$request` or `$request_uri`,
which hold the token, and leaves out the `Referer` header. nginx's error log still includes the
request line when a request fails (for example while the Classroom restarts), so restrict who can
read it.

## LibreOffice and untrusted templates

When the Classroom syncs a Release, it renders the slide backgrounds of the Release's PowerPoint
template with LibreOffice. The template comes from a repository someone else controls, so the
image treats the rendering as untrusted:

- `/usr/local/bin/libreoffice` wraps `soffice` and runs it in a new user and network namespace
  (`unshare --user --map-root-user --net`). Its only interface is a loopback that is down, so a
  template cannot reach the internet, the host, cloud metadata services or the Classroom itself.
- Docker's default seccomp profile allows creating namespaces only with `CAP_SYS_ADMIN`, which the
  container drops. `compose.yaml` therefore uses `seccomp-classroom.json`: Docker's default
  profile plus one rule that allows `unshare` when the only namespaces it asks for are user and
  network. Mount, PID, IPC, UTS and cgroup namespaces stay refused.
- `CLASSROOM_RENDER_SANDBOX=required` (the compose default) never runs LibreOffice outside the
  sandbox: where it cannot be created, the Classroom shows plain slide backgrounds with the same
  copyable text. `auto` (the image default, for `docker run` without the profile) runs
  LibreOffice with the container's network instead. On start, the Classroom logs which applies:
  `LibreOffice renders templates in a sandbox without network access`, or a warning.
- In every case `classroom-hardening.xcd` sets finalized LibreOffice defaults: no macros (highest
  macro security, macro execution disabled), no active content (OLE, DDE, OLE automation), and
  links from the document blocked (`BlockUntrustedRefererLinks`). The image installs no Java
  runtime and no LibreOffice update checker. Each render uses a throwaway profile, runs as the
  unprivileged `classroom` user on a read-only root filesystem with no capabilities, is limited
  to 30 seconds, after `course_harness.core` has checked the template's zip size and entry
  count.

`seccomp-classroom.json` is derived from the default profile of `github.com/moby/profiles/seccomp`
v0.2.3, the version Docker Engine 29.6.0 uses, by
`packages/classroom/docker/make_seccomp_profile.py`; its docstring shows how to regenerate it for
a later Docker release. The sandbox can still be unavailable, for example where the host forbids
unprivileged user namespaces (Ubuntu 24.04's AppArmor restriction, or
`user.max_user_namespaces=0`); with `required` you then get plain backgrounds and a warning in the
log.

The residual risk: a template that exploits a LibreOffice bug runs code as the `classroom` user.
In the sandbox it has no network, but it can read and change what that user can: `/data`,
including the SQLite store with Learner answers. With `auto` and no sandbox it can also use the
container's network: the internet (as the Classroom does to fetch from GitHub), the host through
the bridge gateway `172.29.81.1` including services that listen on all host interfaces, and on a
cloud machine the instance metadata service at `169.254.169.254`, which can hand out the
machine's credentials. Keep `required`, block `169.254.169.254` from containers on cloud hosts
regardless, attach only repositories from Course Authors you trust, and rebuild the image
regularly for LibreOffice security fixes.

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

To restore an archive, replace the volume's contents with it while the service is stopped. The
volume is mounted writable here, and the files are given back to uid 10001:

```bash
docker compose stop classroom
docker run --rm -v course-harness-classroom_classroom-data:/data -v "$PWD":/backup:ro \
  debian:trixie-slim sh -c 'find /data -mindepth 1 -delete \
    && tar -C /data -xzf /backup/classroom-data-2026-11-01.tar.gz \
    && chown -R 10001:10001 /data'
docker compose start classroom
```

To restore only the store from an online copy, put it at `/data/classroom.sqlite3` the same way
(and delete any `classroom.sqlite3-wal` and `-shm` files beside it). The backups contain Learner
names, student IDs and answers: store them as carefully as the server.

## Upgrade

```bash
git pull
docker compose up -d --build
docker image prune
```

Upgrade the Classroom before Course Authors publish Releases with a newer Course Harness. A
Classroom reads Release manifests strictly and refuses one with fields it does not know (its
sync message then says the Release came from a newer Course Harness), so it keeps serving the
previous Release until it is upgraded. For example, Releases with notes ("What changed") need a
Classroom that knows that field.

The Classroom migrates its SQLite store on start, so back it up first. The base images are pinned
by digest, so a rebuild changes them only when the repository does; rebuild without the cache now
and then (`docker compose build --no-cache`) to pick up Debian security updates for LibreOffice
and Git.

## From Course Harness to a shared link

1. **Publish** a Course Release in Course Harness with **Publish** in the header. A Release is a
   Git tag `course-release/<slug>` with a manifest, plus a ref holding its PowerPoint files.
2. **Share** it to a public GitHub repository: tick **Share to …** in the Publish dialog, or use
   **Sync** later. Sharing sends `main`, every Release tag and the Release files refs (see "Course
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

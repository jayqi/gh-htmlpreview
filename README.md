# gh-htmlpreview

[![coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/jayqi/gh-htmlpreview/badges/coverage.json)](https://github.com/jayqi/gh-htmlpreview/actions/workflows/tests.yml?query=branch%3Amain)

A GitHub CLI extension or a standalone Python CLI tool for serving a preview of an HTML file from a GitHub repository. Pass a URL for a file in a GitHub repository, and it will open your browser to a local HTTP server that serves the file and its relative assets (CSS, JavaScript, images, etc.). All files are handled in memory, so no need to download files or clean them up afterwards. It can authenticate with GitHub to work with either public or private repos.

Handy for browsing things like:

- Docs websites, espcially ones in private repos
- LLM-generated rich HTML artifacts
- [Marimo notebook](https://marimo.io/) HTML exports
- [Quarto](http://quarto.org/) reports

> [!IMPORTANT]
> **Security:** The previewed page's JavaScript runs in your browser like any website's. Only preview pages from repositories you trust as much as a website you'd visit. See the [Security](#security) section for additioanl discussion.

> [!NOTE]
> What this tool does is similar to [htmlpreview.github.io](https://htmlpreview.github.io/), but it runs locally and authenticates through the GitHub CLI. It does not route content through a third-party website, and it works for private repos. We are not affiliated in any way with htmlpreview.github.io.

## Installation

### As a GitHub CLI extension

With the [GitHub CLI](https://cli.github.com/) installed, run:

```bash
gh extension install jayqi/gh-htmlpreview
```

The extension needs [uv](https://docs.astral.sh/uv/) or Python 3.11 or newer on your `PATH`. With uv, it finds or downloads a suitable Python itself.

### As a standalone CLI tool

As a Python package from PyPI, with uv:

```bash
uvx gh-htmlpreview URL
```

or installed as a tool:

```bash
uv tool install gh-htmlpreview
```

It has no dependencies beyond the Python standard library.

## Usage

Copy the URL of an HTML file from GitHub and pass it in:

```bash
gh htmlpreview https://github.com/jayqi/gh-htmlpreview/blob/main/demo/index.html
```

```text
Serving jayqi/gh-htmlpreview@a64dfb7:demo/index.html at http://127.0.0.1:53817/demo/index.html
Shutting down after 0:10:00 without requests. Press Ctrl-C to stop.
```

The branch is resolved to a commit when it starts, and every file in the session comes from that commit. The server stops after 10 minutes without requests, or when you press Ctrl-C.

A private repository works the same way once you're signed in to the GitHub CLI:

```bash
gh auth login
gh htmlpreview https://github.com/my-org/private-repo/blob/main/reports/summary.html
```

Branch names containing `/` work too:

```bash
gh htmlpreview https://github.com/my-org/my-repo/blob/feature/new-report/docs/report.html
```

To use the standalone Python CLI installed from PyPI, the command is `gh-htmlpreview` instead of `gh htmlpreview`.

For a shorter command, set a gh alias:

```bash
gh alias set hp htmlpreview
gh hp https://github.com/jayqi/gh-htmlpreview/blob/main/demo/index.html
```

### Accepted URL formats

- `https://github.com/{owner}/{repo}/blob/{ref}/{path}`
- `https://github.com/{owner}/{repo}/raw/{ref}/{path}`
- `https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}`

`{ref}` can be a branch, a tag or a full commit SHA, as in GitHub's permalinks.

### Links between pages

The whole repository at that commit is servable from the HTTP server's root, so relative links (`../index.html`) and root-relative links (`/docs/other.html`) resolve the way they would on GitHub Pages serving from the repository root. Following links from the previewed page to other HTML files in the repository works as a result, but it's best effort: a link to a directory needs a trailing slash (`articles/`) to serve its `index.html`.

### Authentication

This tool uses a GitHub auth token (e.g., `gh auth`, a personal access token, OAuth app token) to authenticate. It checks token sources in the following order:

1. The `GH_TOKEN` environment variable
2. The `GITHUB_TOKEN` environment variable
3. `gh auth token`, if the GitHub CLI is installed and signed in

If generating a token with fine-grained permissions, it needs "Contents: Read-only" (`contents: read`) permissions.

Without a token, public repositories still work. Files come from `raw.githubusercontent.com`, and only resolving the branch uses the GitHub API, which allows 60 unauthenticated requests per hour.

### Opening the browser

The tool will automatically open your browser to the served page. You can configure a browser command, and it follows the GitHub CLI's behavior with the following order:

- The `GH_BROWSER` environment variable
- The `gh config get browser`
- The the `BROWSER` environment variable
- Your system's default browser

Use `--no-browser/-n` to print the URL without opening a browser.

In an SSH session, or on Linux without a display, the default browser isn't opened. Over SSH, it prints the port-forwarding command to run on your own machine, such as `ssh -L 53817:127.0.0.1:53817 HOST`. You can use `--port/-p` to specify a port in order to keep the same port between sessions.

## Security considerations

The previewed page's JavaScript runs in your browser like any website's. **Only preview pages from repositories you trust as much as a website you'd visit.** Because browsers don't separate cookies by port, that JavaScript can read and set cookies of other apps served on `127.0.0.1`, other than HttpOnly ones. Storage the page leaves behind, such as `localStorage`, can also be seen by later web applications served at same port.

The design of this tool takes the following precautions to avoid security problems:

- The server listens on `127.0.0.1` only (and not 0.0.0.0 that allows other devices to connect).
- There is an idle timeout after which the server is shut down. By default, it is 10 minutes.
- Requests must have the `Host` header `127.0.0.1:{port}`, which blocks DNS-rebinding attacks from reading repository content through the server.
- Requests from other sites are refused, based on the `Sec-Fetch-Site` header that browsers send. This includes other apps served on `127.0.0.1`.
- JavaScript service workers can't be registered, so a previewed page can't leave code behind that intercepts a later session.
- A session serves one repository at one commit. It does not mix commits or serve files across repositories.
- Your auth token is only sent to GitHub and is not accessible to the browser.

## Limitations

- Only works for github.com. GitHub Enterprise Server is not supported.
- Git LFS files are served as their pointer files and not the files themselves.

## CLI help

```text
usage: gh htmlpreview [-h] [-t DURATION] [-p PORT] [-n] [--version] URL

Preview an HTML file from a GitHub repository in your browser.

positional arguments:
  URL                   GitHub URL of the file to preview.

options:
  -h, --help            show this help message and exit
  -t, --timeout DURATION
                        Time to keep the server alive after the most recent request,
                        such as '90s', '10m' or '1h'. A number without a unit is
                        seconds. (default: 10m)
  -p, --port PORT       Port to use. (default: 0, chosen by the operating system)
  -n, --no-browser      Print the URL without opening a browser.
  --version             Show version and exit.
```

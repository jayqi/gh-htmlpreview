# gh-htmlpreview

[![coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/jayqi/gh-htmlpreview/badges/coverage.json)](https://github.com/jayqi/gh-htmlpreview/actions/workflows/tests.yml?query=branch%3Amain)

A GitHub CLI extension or a standalone Python CLI tool for serving a preview of an HTML file from a GitHub repository. Pass a URL for a file in a GitHub repository, and it will open your browser to a local HTTP server that serves the file and its relative assets (CSS, JavaScript, images, etc.). You can also follow links to other pages in the repository. All files are handled in memory, so no need to download files or clean them up afterwards. It can authenticate with GitHub to work with either public or private repos.

Handy for browsing things like:

- Static sites and built docs, especially in private repos
- Notebook and report exports from [Jupyter](https://jupyter.org/), [marimo](https://marimo.io/), [Quarto](https://quarto.org/), or R Markdown
- Interactive charts exported to HTML, from libraries like Plotly, Bokeh, or Altair
- HTML prototypes and LLM-generated artifacts

> [!IMPORTANT]
> **Security:** The previewed page's JavaScript runs in your browser like any website's. Only preview pages from repositories you trust as much as a website you'd visit. See the [Security](#security) section for additional discussion.

> [!NOTE]
> What this tool does is similar to [htmlpreview.github.io](https://htmlpreview.github.io/), but it runs locally and can authenticate with your GitHub CLI login or a token. It does not route content through a third-party website, and it works for private repos. We are not affiliated in any way with htmlpreview.github.io.

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

The branch or tag is resolved to a commit when it starts, and every file in the session comes from that commit. The server stops after an idle timeout without requests (by default 10 minutes, configurable with `-t`/`--timeout`), or when you press Ctrl-C.

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

`{ref}` can be a branch, a tag, or a full commit SHA, as in GitHub's permalinks.

### Links between pages

The whole repository at that commit is servable from the HTTP server's root, so relative links (`../index.html`) and root-relative links (`/docs/other.html`) resolve the way they would on GitHub Pages serving from the repository root. Following links from the previewed page to other HTML files in the repository works as a result, but it's best effort: a link to a directory needs a trailing slash (`articles/`) to serve its `index.html`.

Root-relative links assume the site's root is the repository's root. A site built into a subdirectory, such as `site/` or `docs/_build/html/`, works only if its links are relative. Links that leave off both the trailing slash and `.html` (`/about`) won't resolve.

### Authentication

This tool authenticates with any GitHub token that can read the repository, such as the one from `gh auth login` or a personal access token. It checks token sources in the following order:

1. The `GH_TOKEN` environment variable
2. The `GITHUB_TOKEN` environment variable
3. `gh auth token`, if the GitHub CLI is installed and signed in

If you create a personal access token for this, a fine-grained token needs the "Contents: Read-only" permission, and a classic token needs the `repo` scope for private repositories.

Without a token, public repositories still work. Files come from `raw.githubusercontent.com`, and only resolving the branch, tag, or commit uses the GitHub API, which allows 60 unauthenticated requests per hour.

### Opening the browser

The tool will automatically open your browser to the served page. You can configure a browser command, and the configuration source follows the GitHub CLI's behavior with the following order:

- The `GH_BROWSER` environment variable
- `gh config get browser`
- The `BROWSER` environment variable
- Your system's default browser

Use `-n`/`--no-browser` to print the URL without opening a browser.

In an SSH session, or on Linux without a display, the default browser isn't opened. Over SSH, it prints the port-forwarding command to run on your own machine, such as `ssh -L 53817:127.0.0.1:53817 HOST`. Forward to the same port number and open the printed URL as is, with `127.0.0.1` rather than `localhost`, or the server refuses the request. You can use `-p`/`--port` to specify a port in order to keep the same port between sessions.

## Security

The previewed page's JavaScript runs in your browser like any website's. **Only preview pages from repositories you trust as much as a website you'd visit.** Because browsers don't separate cookies by port, that JavaScript can read and set cookies of other apps served on `127.0.0.1`, other than HttpOnly ones. Storage the page leaves behind, such as `localStorage`, can also be seen by later web applications served at the same port.

The design of this tool takes the following precautions to avoid security problems:

- The server listens on `127.0.0.1` only (and not `0.0.0.0`, which would allow other devices to connect).
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

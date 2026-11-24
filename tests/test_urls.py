import pytest

from gh_htmlpreview.urls import FileURL, parse


@pytest.mark.parametrize(
    ("url", "segments"),
    [
        # Blob
        ("https://github.com/octo/repo/blob/main/index.html", ("main", "index.html")),
        (
            "https://github.com/octo/repo/blob/main/docs/report/index.html",
            ("main", "docs", "report", "index.html"),
        ),
        (
            "https://github.com/octo/repo/blob/feature/new-ui/docs/index.html",
            ("feature", "new-ui", "docs", "index.html"),
        ),
        (
            "https://github.com/octo/repo/blob/3f2c1a9d0e8b7c6a5f4e3d2c1b0a9f8e7d6c5b4a/a.html",
            ("3f2c1a9d0e8b7c6a5f4e3d2c1b0a9f8e7d6c5b4a", "a.html"),
        ),
        ("https://github.com/octo/repo/blob/v1.0.0/a.html", ("v1.0.0", "a.html")),
        # Raw redirect
        ("https://github.com/octo/repo/raw/main/docs/a.html", ("main", "docs", "a.html")),
        (
            "https://github.com/octo/repo/raw/refs/heads/main/a.html",
            ("refs", "heads", "main", "a.html"),
        ),
        # Raw host
        (
            "https://raw.githubusercontent.com/octo/repo/main/docs/a.html",
            ("main", "docs", "a.html"),
        ),
        (
            "https://raw.githubusercontent.com/octo/repo/refs/heads/feature/x/a.html",
            ("refs", "heads", "feature", "x", "a.html"),
        ),
        (
            "https://raw.githubusercontent.com/octo/repo/refs/tags/v1.0.0/a.html",
            ("refs", "tags", "v1.0.0", "a.html"),
        ),
        # Query strings and fragments
        ("https://github.com/octo/repo/blob/main/a.html?plain=1", ("main", "a.html")),
        ("https://github.com/octo/repo/blob/main/a.html#L10-L20", ("main", "a.html")),
        (
            "https://raw.githubusercontent.com/octo/repo/main/a.html?token=abc#top",
            ("main", "a.html"),
        ),
        # Percent-encoding
        ("https://github.com/octo/repo/blob/main/my%20report.html", ("main", "my report.html")),
        ("https://github.com/octo/repo/blob/main/caf%C3%A9.html", ("main", "café.html")),
        # Variations
        ("http://github.com/octo/repo/blob/main/a.html", ("main", "a.html")),
        ("HTTPS://GitHub.com/octo/repo/blob/main/a.html", ("main", "a.html")),
        ("  https://github.com/octo/repo/blob/main/a.html\n", ("main", "a.html")),
        ("https://github.com/octo/repo/blob/main/docs//a.html", ("main", "docs", "a.html")),
        ("https://github.com/octo/repo/blob/main/docs/", ("main", "docs")),
    ],
)
def test_parse(url, segments):
    assert parse(url) == FileURL(owner="octo", repo="repo", segments=segments)


@pytest.mark.parametrize(
    ("url", "owner", "repo"),
    [
        ("https://github.com/my-org/my.repo_name/blob/main/a.html", "my-org", "my.repo_name"),
        ("https://github.com/a1/.github/blob/main/a.html", "a1", ".github"),
        ("https://raw.githubusercontent.com/X/Y/main/a.html", "X", "Y"),
        ("https://github.com/octo_acme/repo/blob/main/a.html", "octo_acme", "repo"),
    ],
)
def test_parse_owner_and_repo(url, owner, repo):
    parsed = parse(url)
    assert (parsed.owner, parsed.repo) == (owner, repo)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        # Other GitHub pages
        ("https://github.com/octo/repo", "Unsupported URL"),
        ("https://github.com/octo/repo/tree/main/docs", "Unsupported URL"),
        ("https://github.com/octo/repo/blame/main/a.html", "Unsupported URL"),
        ("https://github.com/octo/repo/pull/1/files", "Unsupported URL"),
        ("https://gist.github.com/octo/abc123", "Unsupported URL"),
        ("https://htmlpreview.github.io/?https://github.com/o/r/blob/main/a.html", "Unsupported"),
        # Other hosts and schemes
        ("https://example.com/octo/repo/blob/main/a.html", "Unsupported URL"),
        ("https://www.github.com/octo/repo/blob/main/a.html", "Unsupported URL"),
        ("https://github.com:8443/octo/repo/blob/main/a.html", "Unsupported URL"),
        ("https://user@github.com/octo/repo/blob/main/a.html", "Unsupported URL"),
        ("ftp://github.com/octo/repo/blob/main/a.html", "Unsupported URL"),
        ("github.com/octo/repo/blob/main/a.html", "Unsupported URL"),
        ("", "Unsupported URL"),
        # Missing ref or path
        ("https://github.com/octo/repo/blob/main", "both a ref and a file path"),
        ("https://github.com/octo/repo/blob/main/", "both a ref and a file path"),
        ("https://raw.githubusercontent.com/octo/repo/main", "both a ref and a file path"),
        ("https://raw.githubusercontent.com/octo/repo", "both a ref and a file path"),
        # Dot segments
        ("https://github.com/octo/repo/blob/main/../a.html", "Unsupported URL"),
        ("https://github.com/octo/repo/blob/main/./a.html", "Unsupported URL"),
        ("https://github.com/octo/repo/blob/main/%2E%2E/a.html", "Unsupported URL"),
    ],
)
def test_parse_invalid(url, message):
    with pytest.raises(ValueError, match=message):
        parse(url)

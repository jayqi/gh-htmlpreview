# gh-htmlpreview

[![coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/jayqi/gh-htmlpreview/badges/coverage.json)](https://github.com/jayqi/gh-htmlpreview/actions/workflows/tests.yml?query=branch%3Amain)

Preview an HTML file from a GitHub repository in your browser. Give it a GitHub URL to the file, and it starts a local HTTP server that serves the file and fetches its relative assets (CSS, JavaScript, images, data) from the same repository and commit on demand.

It does what [htmlpreview.github.io](https://htmlpreview.github.io/) does, but runs locally and authenticates through the GitHub CLI, so it works for private repositories and doesn't route content through a third party. It is not affiliated with htmlpreview.github.io.

This project is in early development and doesn't work yet.

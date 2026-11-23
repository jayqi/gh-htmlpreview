class NotFound(Exception):
    """A file, ref or repository doesn't exist or isn't accessible."""


class UpstreamError(Exception):
    """Any other failure getting something from GitHub."""

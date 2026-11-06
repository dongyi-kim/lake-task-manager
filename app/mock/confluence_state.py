"""Compatibility names; generic document state is owned by jira820."""

from jira820.confluence import ConfluenceError as ConfluenceFixtureError, ConfluenceState

__all__ = ["ConfluenceFixtureError", "ConfluenceState"]

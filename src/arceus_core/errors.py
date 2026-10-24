"""Exception hierarchy for ARCEUS core.

Errors are deliberately narrow and explicit: callers must be able to tell a
missing root from a database problem or an unsupported schema version. Broad
exception swallowing is not used anywhere in this package.
"""

from __future__ import annotations


class ArceusError(Exception):
    """Base class for all expected, user-facing ARCEUS errors."""


class ConfigError(ArceusError):
    """Configuration or environment problem (for example, an unusable data dir)."""


class DatabaseError(ArceusError):
    """The SQLite database cannot be opened, initialized, or is corrupt."""


class SchemaVersionError(DatabaseError):
    """The database was written by a newer, unsupported schema version."""


class RootError(ArceusError):
    """A root registration problem: not a directory, duplicate, or unknown root."""


class RootNotFoundError(RootError):
    """No registered root matches the supplied identifier."""


class ScanError(ArceusError):
    """A scan could not be completed at all (as opposed to partial errors)."""


class PathResolutionError(ArceusError):
    """A path cannot be mapped to exactly one registered root."""


class QueryError(ArceusError):
    """A search or graph query is empty or outside supported limits."""


class InferenceError(ArceusError):
    """Base class for expected, user-facing local-inference failures."""


class InferenceUnavailableError(InferenceError):
    """The requested local backend is unavailable or outside the offline policy."""


class InferenceTimeoutError(InferenceError):
    """A local inference request exceeded its configured timeout."""


class InferenceCancelledError(InferenceError):
    """A local inference request was cancelled before completion."""


class InferenceInputError(InferenceError):
    """A request violates inference input or resource limits."""


class InferenceOutputError(InferenceError):
    """A backend returned malformed output or failed the response contract."""


class InferenceBackendError(InferenceError):
    """A backend raised an unexpected error while serving a request."""


class MemoryStoreError(ArceusError):
    """Invalid episodic-memory or skill-registry record/operation."""


class ControllerError(ArceusError):
    """Base class for cognitive-controller failures."""


class ControllerValidationError(ControllerError):
    """A proposed action failed schema or permission validation."""

class ApplicationError(Exception):
    """Base class for application-specific exceptions."""

class AuthenticationError(ApplicationError):
    """Raised when authentication fails."""

class InvalidCredentialsError(AuthenticationError):
    """Raised when provided credentials are invalid."""

class InactiveUserError(AuthenticationError):
    """Raised when an inactive user attempts to authenticate."""

class EmailAlreadyExistsError(ApplicationError):
    """Raised when attempting to register with an email that already exists."""


class ApplicationError(Exception):
    """Base class for application-specific exceptions."""


class AuthenticationError(ApplicationError):
    """Raised when authentication fails."""


class InvalidCredentialsError(AuthenticationError):
    """Raised when provided credentials are invalid."""


class InvalidRefreshTokenError(AuthenticationError):
    """Raised when a refresh token is invalid, expired, or revoked."""


class InvalidGoogleTokenError(AuthenticationError):
    """Raised when a Google ID token cannot be verified."""


class InactiveUserError(AuthenticationError):
    """Raised when an inactive user attempts to authenticate."""


class EmailAlreadyExistsError(ApplicationError):
    """Raised when attempting to register with an email that already exists."""


class AccountLinkRequiredError(ApplicationError):
    """Raised when a verified Google email already belongs to another account."""


class GoogleAuthUnavailableError(ApplicationError):
    """Raised when Google's identity service cannot be reached."""

class InvalidPasswordResetTokenError(AuthenticationError):
    """Raised when a password reset token is invalid, expired, or revoked."""
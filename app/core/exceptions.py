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


class AvatarStorageUnavailableError(ApplicationError):
    """Raised when the avatar backing store (Google Drive) cannot be reached."""


class InvalidAvatarError(ApplicationError):
    """Raised when an uploaded avatar is not an image this app accepts."""


class AvatarTooLargeError(ApplicationError):
    """Raised when an uploaded avatar exceeds the configured size limit."""


class AvatarNotFoundError(ApplicationError):
    """Raised when a user has no avatar to read or delete."""


class InvalidPasswordResetTokenError(AuthenticationError):
    """Raised when a password reset token is invalid, expired, or revoked."""


class InvalidChallengeOptionsError(ApplicationError):
    """Raised when a challenge's answer options are inconsistent."""


class DuplicateOrderIndexError(ApplicationError):
    """Raised when an order_index collides with a sibling under the same parent."""


class CourseNotFoundError(ApplicationError):
    """Raised when a course cannot be found."""


class UnitNotFoundError(ApplicationError):
    """Raised when a unit cannot be found."""


class LessonNotFoundError(ApplicationError):
    """Raised when a lesson cannot be found."""


class ChallengeNotFoundError(ApplicationError):
    """Raised when a challenge cannot be found."""


class ChallengeOptionNotFoundError(ApplicationError):
    """Raised when a challenge option cannot be found."""


class TopicNotFoundError(ApplicationError):
    """Raised when a topic cannot be found."""


class DuplicateTopicNameError(ApplicationError):
    """Raised when a topic name is already in use."""


class DuoMatchNotFoundError(ApplicationError):
    """Raised when a duo match cannot be found."""


class DuoRoomNotFoundError(ApplicationError):
    """Raised when no live room is waiting behind a room code."""


class NotMatchMemberError(ApplicationError):
    """Raised when a user reads a duo match they did not play in."""


class GameClassNotFoundError(ApplicationError):
    """Raised when a character class code does not exist."""


class SkillNotFoundError(ApplicationError):
    """Raised when a skill cannot be found."""


class SkillAlreadyOwnedError(ApplicationError):
    """Raised when unlocking a skill the user already has."""


class SkillLockedError(ApplicationError):
    """Raised when a skill's unlock conditions are not met yet."""


class NotEnoughGoldError(ApplicationError):
    """Raised when a purchase costs more gold than the user has."""


class InvalidLoadoutError(ApplicationError):
    """Raised when a requested loadout is not one the user can equip."""


class NotEnoughEnergyError(ApplicationError):
    """Raised when a match is started with an empty energy bar."""


class ItemNotFoundError(ApplicationError):
    """Raised when an item cannot be found."""


class InvalidEquipmentError(ApplicationError):
    """Raised when a requested equipment set is not one the user can wear."""


class SeasonNotFoundError(ApplicationError):
    """Raised when there is no active season."""

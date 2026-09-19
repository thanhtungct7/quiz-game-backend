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


class InvalidAnswerSubmissionError(ApplicationError):
    """Raised when an answer's shape does not match the challenge type --
    a single option sent for an ORDER challenge, or a word sequence sent for
    a single-choice one."""


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


class ItemAlreadyOwnedError(ApplicationError):
    """Raised when buying an item the user already has."""


class ItemNotForSaleError(ApplicationError):
    """Raised when buying an item the shop does not stock."""


class InvalidEquipmentError(ApplicationError):
    """Raised when a requested equipment set is not one the user can wear."""


class MonsterUnavailableError(ApplicationError):
    """Raised when no monster in the catalog can guard a lesson."""


class UserNotFoundError(ApplicationError):
    """Raised when a profile is read for a user id that does not exist."""


class BenchmarkExamNotEligibleError(ApplicationError):
    """Raised when a Benchmark Exam result is recorded for a cap the player's
    raw level has not reached yet, or that is not one of `cefr.LEVEL_CAPS`."""


class BenchmarkExamAlreadyClearedError(ApplicationError):
    """Raised when a sitting is started for a cap the player has already cleared."""


class BenchmarkExamAttemptNotFoundError(ApplicationError):
    """Raised when a sitting does not exist or belongs to another player."""


class BenchmarkExamAttemptClosedError(ApplicationError):
    """Raised when an answer is sent to a sitting that has been handed in, has
    run out of time, or was abandoned for a newer one."""


class BenchmarkExamQuestionNotInAttemptError(ApplicationError):
    """Raised when an answer names a challenge that is not on the paper."""


class BenchmarkExamQuestionAlreadyAnsweredError(ApplicationError):
    """Raised when a question on the paper is answered a second time."""


class BenchmarkExamNotEnoughQuestionsError(ApplicationError):
    """Raised when the course holds too little content to draw a paper from."""


class ChallengeLockedByExamError(ApplicationError):
    """Raised when a challenge is checked while it sits on the caller's open
    Benchmark Exam paper -- checking it would reveal the answer."""


class AiUnavailableError(ApplicationError):
    """Raised when the AI provider is not configured, or failed after its retry."""


class ScenarioNotFoundError(ApplicationError):
    """Raised when a conversation is started for a scenario code that does not exist."""


class ConversationNotFoundError(ApplicationError):
    """Raised when a conversation, or a message in it, does not exist or belongs
    to another user."""


class ConversationClosedError(ApplicationError):
    """Raised when a conversation that is finished, or out of turns, is written to."""


class ConversationAwaitingReplyError(ApplicationError):
    """Raised when a new message is sent while the last one still has no reply --
    the client must retry that one first."""


class ConversationTooShortError(ApplicationError):
    """Raised when feedback is asked for before the learner has said anything."""


class DailyConversationLimitError(ApplicationError):
    """Raised when a learner starts more conversations in a day than allowed."""


class DailyQuestNotFoundError(ApplicationError):
    """Raised when a daily quest does not exist or belongs to another user."""


class DailyQuestNotCompletedError(ApplicationError):
    """Raised when a daily quest's reward is claimed before the quest is done."""


class DailyQuestAlreadyClaimedError(ApplicationError):
    """Raised when a daily quest's reward has already been claimed."""


class DailyQuestExpiredError(ApplicationError):
    """Raised when a quest from an earlier day is claimed: each day's set
    expires at local midnight, unclaimed rewards with it."""


class ActivityChestNotFoundError(ApplicationError):
    """Raised when no activity chest sits at the requested milestone."""


class ActivityChestLockedError(ApplicationError):
    """Raised when a chest is opened before today's activity points reach it."""


class ActivityChestAlreadyClaimedError(ApplicationError):
    """Raised when today's chest at that milestone has already been opened."""

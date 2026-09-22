"""YouTube OAuth / connection errors. Never include token values."""


class YoutubeOAuthError(Exception):
    """Base class. Messages must be safe to return to clients."""


class OAuthNotConfiguredError(YoutubeOAuthError):
    """Application Google client credentials or encryption are missing."""


class InvalidOAuthStateError(YoutubeOAuthError):
    """Missing, unknown, expired, or reused OAuth state."""


class AuthorizationDeniedError(YoutubeOAuthError):
    """The user denied Google consent."""


class TokenExchangeError(YoutubeOAuthError):
    """Authorization-code or refresh exchange failed."""


class YoutubeChannelLookupError(YoutubeOAuthError):
    """Authorized identity has no usable YouTube channel."""


class ConnectionPersistError(YoutubeOAuthError):
    """Durable connection write failed."""

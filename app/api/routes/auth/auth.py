from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import (
    AuthServiceDependency,
    PasswordResetServiceDependency,
)
from app.core.exceptions import (
    AccountLinkRequiredError,
    EmailAlreadyExistsError,
    GoogleAuthUnavailableError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidGoogleTokenError,
    InvalidPasswordResetTokenError,
    InvalidRefreshTokenError,
)
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    GoogleLoginRequest,
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.user import UserRead

router = APIRouter()


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, service: AuthServiceDependency) -> User:
    try:
        return await service.register(
            email=str(payload.email),
            password=payload.password,
            username=payload.username,
        )
    except EmailAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, service: AuthServiceDependency) -> TokenResponse:
    try:
        return await service.login(
            email=str(payload.email),
            password=payload.password,
        )
    except (InvalidCredentialsError, InactiveUserError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    payload: RefreshTokenRequest,
    service: AuthServiceDependency,
) -> TokenResponse:
    try:
        return await service.refresh(payload.refresh_token)
    except InvalidRefreshTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshTokenRequest, service: AuthServiceDependency) -> Response:
    await service.logout(payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/google", response_model=TokenResponse)
async def google_login(
    payload: GoogleLoginRequest,
    service: AuthServiceDependency,
) -> TokenResponse:
    try:
        return await service.login_with_google(payload.id_token)
    except InvalidGoogleTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Google ID token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except AccountLinkRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except GoogleAuthUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google authentication is temporarily unavailable",
            headers={"Retry-After": "30"},
        ) from exc


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    payload: ForgotPasswordRequest,
    service: PasswordResetServiceDependency,
) -> dict[str, str]:
    await service.request_password_reset(str(payload.email))
    return {
        "message": (
            "If the email exists, a password reset link has been sent to the "
            "provided email address."
        )
    }


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    payload: ResetPasswordRequest,
    service: PasswordResetServiceDependency,
) -> Response:
    try:
        await service.reset_password(payload.token, payload.new_password)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except InvalidPasswordResetTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
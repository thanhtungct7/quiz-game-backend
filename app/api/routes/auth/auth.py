from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status

from app.api.dependencies import (
    AuthServiceDependency,
    PasswordResetServiceDependency,
)
from app.api.rate_limit import enforce, limit_by_ip
from app.core import rate_limit_policies as limits
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
from app.models.auth.user import User
from app.schemas.auth.auth import (
    ForgotPasswordRequest,
    GoogleLoginRequest,
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.auth.user import UserRead

router = APIRouter()


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[limit_by_ip("register", limits.REGISTER_PER_IP)],
)
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


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[limit_by_ip("login", limits.LOGIN_PER_IP)],
)
async def login(payload: LoginRequest, service: AuthServiceDependency) -> TokenResponse:
    # Per address as well as per IP, so guessing one account's password from many
    # addresses is still slow.
    enforce(f"login:email:{str(payload.email).lower()}", limits.LOGIN_PER_EMAIL)
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


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[limit_by_ip("refresh", limits.REFRESH_PER_IP)],
)
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


@router.post(
    "/google",
    response_model=TokenResponse,
    dependencies=[limit_by_ip("google", limits.GOOGLE_LOGIN_PER_IP)],
)
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


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[limit_by_ip("forgot-password", limits.FORGOT_PASSWORD_PER_IP)],
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    service: PasswordResetServiceDependency,
) -> dict[str, str]:
    # Counted whether or not the address has an account, so a 429 says nothing about it.
    enforce(
        f"forgot-password:email:{str(payload.email).lower()}", limits.FORGOT_PASSWORD_PER_EMAIL
    )
    # The email goes out after this response: see PasswordResetService.request_password_reset.
    await service.request_password_reset(str(payload.email), background_tasks)
    return {
        "message": (
            "If the email exists, a password reset link has been sent to the "
            "provided email address."
        )
    }


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[limit_by_ip("reset-password", limits.RESET_PASSWORD_PER_IP)],
)
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

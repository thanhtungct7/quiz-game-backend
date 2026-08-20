from fastapi import APIRouter, HTTPException, status

from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, RegisterRequest
from app.schemas.user import UserRead
from app.core.exceptions import (InvalidCredentialsError, InactiveUserError)
from app.services import auth_service
from app.services.auth_service import AuthService
from app.api.dependencies import AuthServiceDependency
router = APIRouter()


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, service: AuthServiceDependency) -> User:
    try:
        return await service.register(
            email=str(payload.email),
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest,
                service: AuthService) -> TokenResponse:
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
    


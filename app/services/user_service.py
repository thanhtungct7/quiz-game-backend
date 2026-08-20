from app.core.config import settings

from app.schemas.auth import TokenResponse
from app.core.security import create_access_token, create_refresh_token, verify_password
from app.core.exceptions import (InvalidCredentialsError, InactiveUserError)
from app.repository.user_repository import UserRepository
from app.repository.refresh_token_repository import RefreshTokenRepository

class UserService:
    

    def __init__(self, user_repository):
        self.user_repository = user_repository

    def create_user(self, 
                    email: str, 
                    password: str, 
                    username: str
                    ):
        return self.user_repository.create_user(email=email, password=password, username=username)
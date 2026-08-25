from app.core.security import hash_password
from app.models.auth.user import User
from app.repository.auth.user_repository import UserRepository


class UserService:
    def __init__(self, user_repository: UserRepository) -> None:
        self.user_repository = user_repository

    async def create_user(
        self,
        email: str,
        password: str,
        username: str | None = None,
    ) -> User:
        user = User(email=email, hashed_password=hash_password(password), username=username)
        return await self.user_repository.create_user(user)

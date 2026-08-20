from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    username: str
    is_active: bool
    created_at: datetime
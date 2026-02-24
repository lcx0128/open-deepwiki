from pydantic import BaseModel
from typing import List, Optional, Literal


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant"] = "user"
    content: str = ""


class LLMRequest(BaseModel):
    messages: List[LLMMessage]
    model: str = ""
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False


class LLMResponse(BaseModel):
    content: str = ""
    model: str = ""
    usage: Optional[dict] = None
    finish_reason: Optional[str] = None

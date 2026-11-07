"""Base for every request body. Unknown fields are rejected rather than silently ignored (ASVS V2/V4: a typo or
an injected `status`/`role` field must fail loudly, never be half-applied)."""
from pydantic import BaseModel, ConfigDict


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)

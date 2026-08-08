from typing import Callable, Any, Dict, List, Optional
from pydantic import BaseModel

class ToolResult(BaseModel):
    success: bool
    output: str
    data: Optional[Any] = None

class BaseTool:
    name: str
    description: str

    def execute(self, **kwargs) -> ToolResult:
        raise NotImplementedError("Tool execution must be implemented in subclass")

    def to_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description
        }

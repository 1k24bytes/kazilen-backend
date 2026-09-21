from pydantic import BaseModel
from typing import Optional, List

class WorkerResponseSchema(BaseModel):
    id: int
    full_name: str
    phone_number: str
    profile_photo: Optional[str] = None
    rating: Optional[float] = None
    reviews_count: Optional[int] = 0
    locality: str = "Dharampeth, Nagpur"
    eta: str = "Arrives in 30 mins"
    jobs_completed: str = "0"

class WorkerListResponseSchema(BaseModel):
    status: str
    workers: List[WorkerResponseSchema]

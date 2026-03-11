from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict
from app.models.refund import RefundStatus, RefundSource
from app.models.file_attachment import FileType


class RefundItemCreate(BaseModel):
    article: str
    brand: Optional[str] = None
    quantity: int = 1
    price: Decimal = Decimal("0")
    description: Optional[str] = None
    position_id: Optional[str] = None
    comment: Optional[str] = None


class RefundItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article: str
    brand: Optional[str]
    quantity: int
    price: Decimal
    description: Optional[str]
    position_id: Optional[str]
    comment: Optional[str]


class FileAttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    file_type: FileType
    file_size: int
    uploaded_at: datetime


class RefundCreate(BaseModel):
    client_name: str
    supplier_id: Optional[int] = None
    order_id: Optional[str] = None
    reason: Optional[str] = None
    items: List[RefundItemCreate] = []


class RefundStatusUpdate(BaseModel):
    status: RefundStatus


class RefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_id: str
    status: RefundStatus
    source: RefundSource
    client_name: str
    client_user_id: Optional[int]
    supplier_id: Optional[int]
    order_id: Optional[str]
    reason: Optional[str]
    created_at: datetime
    updated_at: datetime
    email_subject: Optional[str]
    email_from: Optional[str]
    items: List[RefundItemResponse] = []
    files: List[FileAttachmentResponse] = []


class RefundListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_id: str
    status: RefundStatus
    source: RefundSource
    client_name: str
    supplier_id: Optional[int]
    order_id: Optional[str]
    created_at: datetime
    updated_at: datetime

from decimal import Decimal
from typing import List, Optional
from uuid import UUID
import uuid
from ninja import Field, ModelSchema, Schema
from redis.utils import str_if_bytes
from .models import Customer, Worker, History
from pydantic_extra_types.phone_numbers import PhoneNumber
from pydantic import field_validator
from datetime import datetime, date


class checkPhone(Schema):
    exists: bool
    userID: Optional[str]


class CustomerSchema(Schema):
    id: uuid.UUID
    name: str
    address: Optional[str] = None
    phoneNo: str
    email: str
    photo: Optional[str] = None
    gender: str
    dob: date

    @staticmethod
    def resolve_phoneNo(obj):
        return str(obj.phoneNo)


class WorkerSchema(ModelSchema):
    #    subcategory: List[str]
    phoneNo: str
    imageURL: Optional[str]

    class Meta:
        model = Worker
        fields = "__all__"

    @staticmethod
    def resolve_phoneNo(obj):
        if not obj.phoneNo:
            return None
        return str(obj.phoneNo)


class HistorySchema(Schema):
    id: int
    action: str
    timestmp: datetime
    customer_name: str

    @staticmethod
    def resolve_customer_name(obj):
        return obj.customer.name


class SendOTPSchema(Schema):
    phone: str


class VerifyOTPSchema(Schema):
    phone: str
    otp: str


class CreateAccountSchema(Schema):
    name: str
    phoneNo: PhoneNumber
    email: Optional[str] = None
    gender: str
    dob: date


class booking(Schema):
    worker: str
    customer: str
    action: str

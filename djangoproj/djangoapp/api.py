import uuid
from django.db.models import Q, BooleanField, QuerySet
from twilio.rest.ip_messaging.v2.service import channel
from typing_extensions import List
from typing import List, Optional
from ninja import FilterSchema, NinjaAPI, Query, Router, Schema
from django.shortcuts import get_object_or_404
from .models import Customer, Worker, History
from .schemas import (
    CustomerSchema,
    WorkerSchema,
    HistorySchema,
    SendOTPSchema,
    VerifyOTPSchema,
    CreateAccountSchema,
    booking,
)

import hashlib
from .utils.otp_generator import otp_gen
from .utils.send_otp import sendOTP_SMS, sendOTP_WHATSAPP
from redis import Redis
from dotenv import load_dotenv
import os
import logging
import secrets
from .auth import CustomAuth
from django.db import connections
from django.db.utils import OperationalError


load_dotenv()

api = Router()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
redis_client = Redis(
    host=os.getenv("REDIS_URL"),
    port=int(os.getenv("REDIS_PORT")),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True,
)


@api.get("/worker", response=List[WorkerSchema])
def getAllWorker(request):
    return Worker.objects.all()


@api.get("/filterworker", response=List[dict])
def getFilterWorker(request, category: str):
    tempWor = Worker.objects.all()
    filterWorker = tempWor.filter(
        **{f"sub_categories__{category}__visible": True})
    result = []
    for worker in filterWorker:
        sub_data = worker.sub_categories.get(category, {})

        worker_dict = {
            "id": str(worker.id),
            "name": worker.name,
            "address": worker.address,
            "phoneNo": str(worker.phoneNo),
            "rating": worker.rating,
            "description": worker.description,
            "sub_categories": {
                "price": sub_data.get("price", 120),
                "details": sub_data.get("details", "")
            }
        }
        result.append(worker_dict)

    return result


@api.post("/send-otp")
def send_otp(request, payload: SendOTPSchema):
    phone = payload.phone
    otp = otp_gen()
    logger.info(f"OTP: {otp}")
    hashed = hashlib.sha256(otp.encode()).hexdigest()
    logger.info(f"Hashed: {hashed}")
    redis_client.setex(f"otp:{phone}", 600, hashed)
    logger.info("STORED IN REDIS")
    sendOTP_SMS(otp=otp, recpient=phone)
    return {"status": True, "message": "OTP Sent successfully"}


@api.post("/verify-otp")
def verify_otp(request, payload: VerifyOTPSchema):
    key = f"otp:{payload.phone}"
    stored = redis_client.get(key)
    if not stored:
        return {"success": False, "error": "OTP expired or invalid"}
    input_hash = hashlib.sha256(payload.otp.encode()).hexdigest()
    if input_hash != stored:
        return {"success": False, "error": "Invalid OTP entered"}
    session_token = secrets.token_urlsafe(32)
    logger.info(f"SESSION_TOKEN: {session_token}")
    redis_client.setex(f"session:{session_token}", 604800, payload.phone)
    logger.info("SESSION TOKEN STORED IN REDIS")
    return {"success": True, "session_token": session_token}


class phonePayload(Schema):
    phone: str


@api.post("/check", response={200: dict, 404: dict})
def unprotected_check(request, data: phonePayload):
    valid_phone = "+91" + data.phone
    exists = Customer.objects.filter(phoneNo=valid_phone).first()
    if exists:
        return 200, {"exists": True, "userId": exists.id}
    else:
        return 404, {"messg": "yo no bud"}


class userIdGETTT(Schema):
    userId: uuid.UUID


@api.post("/get-profile", auth=CustomAuth(), response=CustomerSchema)
def get_profile(request, payload: userIdGETTT):
    details = get_object_or_404(Customer, id=payload.userId)
    return details


@api.get("/get-history", auth=CustomAuth(), response=List[HistorySchema])
def get_history(request):
    phone = request.auth
    customer = get_object_or_404(Customer, phoneNo=phone)
    details = History.objects.filter(customer=customer).order_by("-timestmp")
    return details


@api.post("/create-account")
def create_account(request, payload: CreateAccountSchema):
    customer = Customer.objects.create(**payload.dict())
    return {"message": "User created successfully", "userId": customer.id}


@api.post("/requestBooking")
def requestBooking(request, payload: booking):
    customerB = get_object_or_404(Customer, id=payload.customer)
    workerB = get_object_or_404(Worker, id=payload.worker)
    Booking = History.objects.create(
        customer=customerB, worker=workerB, action=payload.action
    )
    customerB.temp_id = Booking.id
    workerB.temp_id = Booking.id
    customerB.save()
    workerB.save()


class userID(Schema):
    userId: str


@api.post("/get-book-status")
def getStatusBook(request, payload: userID):
    customer = get_object_or_404(Customer, id=payload.userId)
    action = get_object_or_404(History, id=customer.work_id)
    return {
        "name": action.worker.name,
        "price": action.price,
        "location": action.geo_location,
    }


class poll_this(Schema):
    userId: str


@api.post("/poll", auth=CustomAuth())
def pollThis(request, payload: poll_this):
    customerA = get_object_or_404(Customer, id=payload.userId)
    if customerA.work_id is not None:
        return {"book": True}
    else:
        return {"book": False}


@api.get("/health")
def helchek(request):
    return {"status": "RUNNING"}


@api.get("/db_health")
def db_check(request):
    db_conn = connections["default"]  # will change once we migrate to neon
    try:
        with db_conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            return {"status": "DB is up"}
    except OperationalError as e:
        print(f"DB ERROR: {e}")  # testing purposes only
        return {"status": "DB is down"}

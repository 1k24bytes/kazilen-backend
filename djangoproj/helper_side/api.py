from uuid import UUID
from djangoapp.models import Customer, History, Worker
from djangoapp.schemas import WorkerSchema
from django.db.models import Q, QuerySet
from typing_extensions import List
from typing import List
from ninja import Router, Schema
from django.shortcuts import get_object_or_404
import hashlib
from djangoapp.utils.otp_generator import otp_gen
from djangoapp.utils.send_otp import sendOTP_SMS, sendOTP_WHATSAPP
from redis import Redis
from dotenv import load_dotenv
import os
import logging
import secrets
from djangoapp.auth import CustomAuth
from django.db import connections
from django.db.utils import OperationalError
from djangoapp.schemas import (
    HistorySchema,
    SendOTPSchema,
    VerifyOTPSchema,
    WorkerSchema,
)
from .schemas import phonePayload, CreateWorkerSchema


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
    redis_client.setex(f"session:{session_token}", 86400, payload.phone)
    logger.info("SESSION TOKEN STORED IN REDIS")
    return {"success": True, "session_token": session_token}


@api.post("/check", response={200: dict, 404: dict})
def unprotected_check(request, data: phonePayload):
    valid_phone = "+91" + data.phone
    exists = Worker.objects.filter(phoneNo=valid_phone).first()
    if exists:
        return 200, {"exists": True, "userId": exists.id}
    else:
        return 404, {"messg": "yo no bud"}


class getPro(Schema):
    userId: UUID


@api.post("/get-profile", auth=CustomAuth(), response=WorkerSchema)
def get_profile(request, payload: getPro):
    data = get_object_or_404(Worker, id=payload.userId)
    return data


@api.get("/get-history", auth=CustomAuth(), response=List[HistorySchema])
def get_history(request):
    phone = request.auth
    if not phone:
        return {"error": "User does not exist", "status": False}
    customer = get_object_or_404(Customer, phoneNo=phone)
    details = History.objects.filter(customer=customer).order_by("-timestmp")
    return details


@api.post("/create-account")
def create_worker(request, payload: CreateWorkerSchema):
    worker = Worker.objects.create(**payload.dict())
    return {"message": "User created successfully", "userId": worker.id}



class giveSub(Schema):
    userId: UUID


@api.post("/getSubCat", response=list)
def giveSubCat(request, payload: giveSub):
    clean_id = payload.userId
    all_dat = get_object_or_404(Worker, id=clean_id)
    return all_dat.sub_categories


class UpdateSubSchema(Schema):
    userId: UUID 
    subCategories: dict

@api.post("/update-subCategories")
def update_worker_subcategories(request, data: UpdateSubSchema):
    worker = get_object_or_404(Worker, id=data.userId)
    subCategories = worker.sub_categories
    for cat_name, new_data in data.subCategories.items():
        if cat_name in subCategories:
            subCategories[cat_name].update({
                "price": new_data.get("price", subCategories[cat_name].get("price")),
                "details": new_data.get("details", subCategories[cat_name].get("details")),
                "visible": new_data.get("visible", subCategories[cat_name].get("visible")),
                })
    worker.save(update_fields=['sub_categories'])
    return {"success": True}


class accept_booking(Schema):
    userId: UUID
    usr: str
    accept: bool


@api.post("/acceptBooking", auth=CustomAuth())
def acceptBooking(request, payload: accept_booking):
    worker_ = get_object_or_404(Worker, id=payload.userId)
    worker_ = get_object_or_404(Worker, id=payload.usr)
    work = get_object_or_404(History, id=worker_.temp_id)
    customerB = work.customer
    worker_.is_working = True
    if not payload.accept:
        worker_.temp_id = None
        worker_.is_working = False
        customerB.temp_id = None
        worker_.save()
        customerB.save()
        return 200
    worker_.work_id = work.id
    worker_.temp_id = None
    customerB.temp_id = None
    worker_.save()
    customerB.save()
    return 200


class getBooking(Schema):
    userId: UUID
    userId: str


@api.post("/get-book", auth=CustomAuth())
def getbooking(request, payload: getBooking):
    worker = get_object_or_404(Worker, id=payload.userId)
    return {"work": worker.work_id, "request": worker.temp_id}

class getaction(Schema):
    userId:UUID 

@api.post("/get-action")
def getAction(request, payload: getaction):
    action = get_object_or_404(History, id=payload.userId)
    id : str

@api.post("/get-action")
def getAction(request, payload: getaction):
    action = get_object_or_404(History, id=payload.id)
    customer_ = action.customer
    return {
        "action": action.action,
        "customer": customer_.name,
        "location": customer_.location,
        "time": action.timestmp,
    }


class poll_this(Schema):
    userId: UUID
    userId: str


@api.post("/poll", auth=CustomAuth())
def pollThis(request, payload: poll_this):
    workerA = get_object_or_404(Worker, id=payload.userId)
    Request = workerA.temp_id is not None
    work = workerA.work_id is not None
    return {"work": work, "request": Request}


class customer_profile(Schema):
    userId: UUID 


@api.post("/get_user_profile")
def unporc_get_profile(request, customer_profile):
    user_id = request.userId
    user = get_object_or_404(Customer, userID=user_id)
    return user


@api.get("/db_health")
def db_check(request):
    db_conn = connections["default"]
    try:
        with db_conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            return {"status": "DB is up"}
    except OperationalError as e:
        print(f"DB ERROR: {e}")  # testing purposes only
        return {"status": "DB is down"}

@api.get("/health")
def helchek(request):
    return {"status": "RUNNING"}

import logging
from fastapi import FastAPI, HTTPException, Request, Response, Depends, APIRouter
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy.orm import Session
from sqlalchemy import text
import os
import json
import hashlib
import jwt
import uuid
from redis import Redis
from datetime import datetime, timedelta
from dotenv import load_dotenv
from database import sessionLocal, Workers
from utils.ot_gen import otp_gen
from utils.send_mess import send_sms
from utils.send_otp import sendOTP_SMS
from schema import SendOTPSchema, VerifyOTPSchema, CreateSchema, CheckSchema
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from metric import VERIF_OTP, OTP_ERRORS

load_dotenv()
app = FastAPI()
router = APIRouter(prefix="/workers")
trace.set_tracer_provider(TracerProvider())
span_processor = BatchSpanProcessor(
    OTLPSpanExporter(endpoint="http://otel-collector:4317", insecure=True)
)
trace.get_tracer_provider().add_span_processor(span_processor)
FastAPIInstrumentor.instrument_app(app)
RequestsInstrumentor().instrument()
JWT_SECRET = os.getenv("JWT_SECRET")
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("worker")
redis_client = Redis(
    host=os.getenv("REDIS_HOST"),
    port=int(os.getenv("REDIS_PORT")),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True,
)


def get_db():
    db = sessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/")
def chek():
    return {"status": "Running"}


@router.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/send-otp")
def send_otp(payload: SendOTPSchema):
    phone = payload.phone
    otp = otp_gen()
    logger.info(json.dumps({"event": "otp_generated"}))
    hashed = hashlib.sha256(otp.encode()).hexdigest()
    redis_client.setex(f"otp:{phone}", 600, hashed)
    logger.info(json.dumps({"event": "otp_stored"}))
    sendOTP_SMS(otp=otp, recpient=phone)
    VERIF_OTP.inc()
    return {"status": True, "message": "OTP SENT SUCCESSFULLY"}


@router.post("/verify-otp")
def verify_otp(payload: VerifyOTPSchema):
    key = f"otp:{payload.phone}"
    stored = redis_client.get(key)
    if not stored:
        logger.error(json.dumps({"event": "otp_missing"}))
        raise HTTPException(status_code=404, detail="otp not found")
    ot = payload.otp
    input_hash = hashlib.sha256(ot.encode()).hexdigest()
    if input_hash != stored:
        OTP_ERRORS.inc()
        logger.error(json.dumps({"event": "invalid_otp"}))
        raise HTTPException(status_code=401, detail="wrong OTP entered")
    pay = {
        "iss": "kazilen-auth",
        "sub": payload.phone,
        "exp": datetime.utcnow() + timedelta(seconds=600),
    }
    token = jwt.encode(pay, JWT_SECRET, algorithm="HS256")
    redis_client.delete(key)
    return {"token": token}


@router.post("/check")
def db_check(response: Response, payload: CheckSchema, db: Session = Depends(get_db)):
    token = payload.token
    pay = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    phone = pay.get("sub")
    valid_phone = phone
    cus = db.query(Workers).filter(Workers.phone == valid_phone).first()
    if not cus:
        logger.error(json.dumps({"event": "user_not_found", "phone": valid_phone}))
        raise HTTPException(status_code=404, detail="User not found")
    payl = {
        "iss": "kazilen-auth",
        "sub": cus.worker_id,
        "exp": datetime.utcnow() + timedelta(days=7),
    }
    ref_token = jwt.encode(payl, JWT_SECRET, algorithm="HS256")
    response.set_cookie(
        key="ref_token",
        value=ref_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=604800,
    )
    logger.info(
        json.dumps({"event": "token_set", "message": "refresh token set in cookies"})
    )
    return {"message": "user found ji..."}


@router.post("/get-profile")
def get_profile(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("ref_token")
    if not token:
        logger.error(json.dumps({"event": "token_not_found"}))
        raise HTTPException(status_code=401, detail="no tokens found")
    pay = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    worker_id = pay.get("sub")
    cus = db.query(Workers).filter(Workers.worker_id == worker_id).first()
    if not cus:
        logger.error(json.dumps({"event": "worker not found", "worker_id": worker_id}))
        raise HTTPException(status_code=404, detail="user not found")
    return {
        "gender": cus.gender,
        "name": cus.name,
        "address": cus.address,
        "phone": cus.phone,
        "dob": cus.dob,
        "rating": cus.rating,
        "categories": cus.categories,
        "sub_categories": cus.sub_categories,
    }


@router.post("/logout")
def logou(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get("ref_token")
    if not token:
        logger.error(json.dumps({"event": "token_not_found"}))
        raise HTTPException(status_code=401, detail="no token found")
    response.delete_cookie("ref_token")
    return {"message": "logged out successfully"}


@router.post("/create-account")
def create_acc(payload: CreateSchema, db: Session = Depends(get_db)):
    gender, name = payload.gender, payload.name
    address, phone = payload.address, payload.phone
    dob, categories = payload.dob, payload.categories
    sub_categories = payload.sub_categories
    worker_id = str(uuid.uuid4())
    wor = db.query(Workers).filter(Workers.phone == phone).first()
    if wor:
        logger.info(json.dumps({"event": "duplicate_worker_attempt", "phone": phone}))
        raise HTTPException(status_code=409, detail="worker already exists")
    if not wor:
        pass
    db_note = Workers(
        gender=gender,
        worker_id=worker_id,
        name=name,
        address=address,
        phone=phone,
        dob=dob,
        categories=categories,
        sub_categories=sub_categories,
    )
    db.add(db_note)
    try:
        db.commit()
        logger.info(json.dumps({"event": "worker_created"}))
    except Exception as e:
        db.rollback()
        logger.error(json.dumps({"event": "db_error", "error": str(e)}))
        raise HTTPException(status_code=500, detail="database error")
    db.refresh(db_note)
    return {"message": f"worker created: {db_note.worker_id}"}


@router.get("/list-workers")
def lis_workers(db: Session = Depends(get_db)):
    workers = db.query(Workers).all()
    res = []
    for worker in workers:
        det = {
            "gender": worker.gender,
            "name": worker.name,
            "address": worker.address,
            "phone": worker.phone,
            "worker_id": worker.worker_id,
            "worker_status": worker.is_active,
            "rating": worker.rating,
            "description": worker.description,
            "categories": worker.categories,
            "sub_categories": worker.sub_categories,
        }
        res.append(det)
    return {"workers": res}


@router.post("/get-history")
def get_his(request: Request, db: Session = Depends(get_db)):
    pass


@router.post("/details")
async def get_det(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
        start_otp = body.get("start_otp", None)
        customer_phone = body.get("customer_phone")
        worker_id = body.get("worker_id")
        worker = db.query(Workers).filter(Workers.worker_id == worker_id).first()
        if not worker:
            raise HTTPException(status_code=404, detail="worker does not exist")
        return {
            "worker_name": worker.name,
            "worker_status": worker.is_active,
            "worker_id": worker.worker_id,
            "worker_phone": worker.phone,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="request failed")


@router.post("/status-update")
async def status_up(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
        worker_id = body.get("worker_id")
        status = body.get("status")
        if worker_id is None:
            raise HTTPException(status_code=404, detail="no worrker id provided")
        worker = db.query(Workers).filter(Workers.worker_id == worker_id).first()
        if not worker:
            raise HTTPException(status_code=404, detail="no worker found")
        if not worker.is_active:
            raise HTTPException(status_code=403, detail="worker is not active")
        if status == "in-progress":
            worker.is_working = True
            db.add(worker)
            try:
                db.commit()
                return {"message": "worker status updated"}
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=500, detail="database error")
        if status == "completed":
            worker.is_working = False
            db.add(worker)
            try:
                db.commit()
                return {"message": "worker status updated"}
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=500, detail="database error")
        raise HTTPException(status_code=404, detail="status not valid")
    except Exception as e:
        raise HTTPException(status_code=500, detail="error fetching request")


@router.get("/get-worker/{worker_id}")
def get_work(worker_id: str, db: Session = Depends(get_db)):
    work = db.query(Workers).filter(Workers.worker_id == worker_id).first()
    if not work:
        raise HTTPException(status_code=404, detail="no worker found")
    return {
        "name": work.name,
        "gender": work.gender,
        "address": work.address,
        "phone": work.phone,
        "worker_id": work.worker_id,
        "is_working": work.is_working,
        "is_active": work.is_active,
        "rating": work.rating,
        "description": work.description,
        "categories": work.categories,
        "sub_categories": work.sub_categories,
    }


@router.get("/health")
def db_chek(db: Session = Depends(get_db)):
    db_status, redis_status = "up", "up"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"
    try:
        redis_client.ping()
    except Exception:
        redis_status = "down"
    overall = "healthy" if db_status == "up" and redis_status == "up" else "degraded"
    return {"overall": overall, "db_status": db_status, "redis_status": redis_status}


app.include_router(router)

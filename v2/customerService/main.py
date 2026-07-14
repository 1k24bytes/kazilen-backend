import logging
from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Response,
    Request,
    APIRouter,
    Response,
)
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
import os
import json
import hashlib
import jwt
from redis import Redis
from utils.ot_gen import otp_gen
from utils.send_otp import sendOTP_SMS
from datetime import datetime, timedelta
from schema import SendOTPSchema, VerifyOTPSchema, CheckSchema, CreateSchema
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from dotenv import load_dotenv
from metric import VERIF_OTP, OTP_SMS, OTP_ERRORS
from database import sessionLocal, Customers
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

load_dotenv()
JWT_SECRET = os.getenv("JWT_SECRET")


def get_db():
    db = sessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI()
router = APIRouter(prefix="/customers")
trace.set_tracer_provider(TracerProvider())
span_processor = BatchSpanProcessor(
    OTLPSpanExporter(endpoint="http://otel-collector:4317", insecure=True)
)
trace.get_tracer_provider().add_span_processor(span_processor)
FastAPIInstrumentor.instrument_app(app)
RequestsInstrumentor().instrument()
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("customer")
redis_client = Redis(
    host=os.getenv("REDIS_HOST"),
    port=int(os.getenv("REDIS_PORT")),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True,
)


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
    VERIF_OTP.inc()
    logger.info(json.dumps({"event": "otp_generated"}))
    hashed = hashlib.sha256(otp.encode()).hexdigest()
    redis_client.setex(f"otp:{phone}", 600, hashed)
    logger.info(json.dumps({"event": "otp_stored"}))
    sendOTP_SMS(otp=otp, recpient=phone)
    OTP_SMS.inc()
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
    cus = db.query(Customers).filter(Customers.phone == valid_phone).first()
    if not cus:
        logger.error(json.dumps({"event": "user_not_found", "phone": valid_phone}))
        raise HTTPException(status_code=404, detail="User not found")
    payl = {
        "iss": "kazilen-auth",
        "sub": phone,
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
    phone = pay.get("sub")
    cus = db.query(Customers).filter(Customers.phone == phone).first()
    if not cus:
        logger.error(json.dumps({"event": "customer not found", "phone": phone}))
        raise HTTPException(status_code=404, detail="user not found")
    return {
        "gender": cus.gender,
        "name": cus.name,
        "phone": cus.phone,
        "dob": cus.dob,
        "address": cus.address,
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
    name, phone = payload.name, payload.phone
    address = payload.address
    gender, dob = payload.gender, payload.dob
    custo = db.query(Customers).filter(Customers.phone == phone).first()
    if custo:
        logger.warning(json.dumps({"event": "duplicate_attempt", "phone": phone}))
        raise HTTPException(status_code=409, detail="customer already exists")
    if not custo:
        pass
    db_note = Customers(name=name, phone=phone, gender=gender, dob=dob, address=address)
    db.add(db_note)
    try:
        db.commit()
        logger.info(json.dumps({"event": "account_created", "id": db_note.id}))
    except Exception as e:
        db.rollback()
        logger.error(json.dumps({"event": "db_error", "error": str(e)}))
        raise HTTPException(status_code=500, detail="database error")
    db.refresh(db_note)
    return JSONResponse(status_code=200, content={"message": "user created success"})


@router.post("/get-history")
def get_his(request: Request, db: Session = Depends(get_db)):
    pass


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

import hashlib
import hmac
import json
import urllib.parse
import urllib.request
import secrets
from fastapi.responses import RedirectResponse
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.security import (
    hash_password,
    verify_password,
    create_access_token,
)
from app.deps import get_current_user
from app.config import settings
from app.services.email import send_password_reset_code, send_registration_code


GOOGLE_ONLY_PREFIX = "google-oauth:"  # marker stored instead of a password hash for Google-created accounts

router = APIRouter(
    prefix="/api/auth",
    tags=["auth"],
)


def serialize_user(
    user: models.User,
) -> schemas.UserOut:
    equipment = []

    if isinstance(user.equipment, list):
        equipment = [
            str(item).strip()
            for item in user.equipment
            if str(item).strip()
        ]

    elif isinstance(user.equipment, str):
        equipment = [
            item.strip()
            for item in user.equipment.split(",")
            if item.strip()
        ]

    return schemas.UserOut(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        avatar_url=user.avatar_url,
        gender=user.gender,
        is_admin=bool(user.is_admin),
        fitness_level=user.fitness_level,
        goal=user.goal,
        training_frequency=user.training_frequency,
        equipment=equipment,
        age=user.age,
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        profile_completed=user.profile_completed,
        created_at=user.created_at,
    )


@router.post("/register")
def register(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()
    full_name = payload.full_name.strip()
    if len(full_name) < 2:
        raise HTTPException(status_code=422, detail="Please enter your full name.")
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=400, detail="An account with this email already exists. Please log in.")
    existing = db.query(models.PendingRegistration).filter(models.PendingRegistration.email == email).first()
    if existing:
        age = (datetime.now(timezone.utc) - existing.created_at.replace(tzinfo=timezone.utc)).total_seconds()
        if age < settings.registration_resend_seconds:
            raise HTTPException(status_code=429, detail=f"Please wait {settings.registration_resend_seconds - int(age)} seconds before requesting another code.")
        db.delete(existing); db.commit()
    code = f"{secrets.randbelow(1_000_000):06d}"
    pending = models.PendingRegistration(full_name=full_name, email=email, hashed_password=hash_password(payload.password), code_hash=hashlib.sha256(code.encode()).hexdigest(), expires_at=datetime.now(timezone.utc)+timedelta(minutes=settings.registration_code_expire_minutes))
    db.add(pending)
    try:
        db.flush()
        send_registration_code(email, code)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="We could not send the verification email. Please try again later.") from exc
    return {"detail": "Verification code sent. Check your email.", "email": email}


@router.post("/verify-registration", response_model=schemas.TokenResponse)
def verify_registration(payload: schemas.RegistrationVerification, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=400, detail="This email is already registered. Please log in.")
    pending = db.query(models.PendingRegistration).filter(models.PendingRegistration.email == email).first()
    if not pending:
        raise HTTPException(status_code=400, detail="No pending registration found. Start registration again.")
    now = datetime.now(timezone.utc); expires = pending.expires_at.replace(tzinfo=timezone.utc) if pending.expires_at.tzinfo is None else pending.expires_at
    if expires < now:
        db.delete(pending); db.commit(); raise HTTPException(status_code=400, detail="This verification code has expired. Start registration again.")
    if pending.attempts >= 5:
        db.delete(pending); db.commit(); raise HTTPException(status_code=400, detail="Too many incorrect attempts. Start registration again.")
    supplied = hashlib.sha256(payload.code.encode()).hexdigest()
    if not secrets.compare_digest(supplied, pending.code_hash):
        pending.attempts += 1; db.commit(); raise HTTPException(status_code=400, detail="Incorrect verification code.")
    user = models.User(full_name=pending.full_name, email=pending.email, hashed_password=pending.hashed_password, profile_completed=False)
    db.add(user); db.delete(pending)
    try:
        db.commit(); db.refresh(user)
    except IntegrityError:
        db.rollback(); raise HTTPException(status_code=400, detail="An account with this email already exists.")
    token = create_access_token(subject=str(user.id))
    return schemas.TokenResponse(access_token=token, user=serialize_user(user))


@router.post("/resend-registration-code")
def resend_registration_code(payload: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()
    pending = db.query(models.PendingRegistration).filter(models.PendingRegistration.email == email).first()
    if not pending: raise HTTPException(status_code=404, detail="No pending registration found.")
    age = (datetime.now(timezone.utc) - pending.created_at.replace(tzinfo=timezone.utc)).total_seconds()
    if age < settings.registration_resend_seconds: raise HTTPException(status_code=429, detail=f"Please wait {settings.registration_resend_seconds - int(age)} seconds before requesting another code.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    pending.code_hash = hashlib.sha256(code.encode()).hexdigest(); pending.expires_at = datetime.now(timezone.utc)+timedelta(minutes=settings.registration_code_expire_minutes); pending.attempts = 0; pending.created_at = datetime.now(timezone.utc)
    try: send_registration_code(email, code); db.commit()
    except Exception as exc: db.rollback(); raise HTTPException(status_code=503, detail="We could not send the verification email.") from exc
    return {"detail":"A new verification code was sent."}


@router.post(
    "/login",
    response_model=schemas.TokenResponse,
)
def login(
    payload: schemas.UserLogin,
    db: Session = Depends(get_db),
):
    user = (
        db.query(models.User)
        .filter(
            models.User.email
            == str(payload.email).strip().lower()
        )
        .first()
    )

    # CHANGE: say what is actually wrong so a missing account is not mistaken for a wrong password.
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No account found for this email. Create an account first.",
        )
    if user.hashed_password.startswith(GOOGLE_ONLY_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account uses Google sign-in. Click Continue with Google.",
        )
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password. Try again or reset it.",
        )

    token = create_access_token(
        subject=str(user.id),
    )

    return schemas.TokenResponse(
        access_token=token,
        user=serialize_user(user),
    )


@router.get(
    "/me",
    response_model=schemas.UserOut,
)
def read_current_user(
    current_user: models.User = Depends(
        get_current_user,
    ),
):
    return serialize_user(current_user)


@router.put(
    "/me",
    response_model=schemas.UserOut,
)
def update_current_user(
    payload: schemas.UserUpdate,
    current_user: models.User = Depends(
        get_current_user,
    ),
    db: Session = Depends(get_db),
):
    update_data = payload.model_dump(
        exclude_unset=True,
    )

    for field, value in update_data.items():
        setattr(
            current_user,
            field,
            value,
        )

    db.commit()
    db.refresh(current_user)

    return serialize_user(current_user)


@router.put(
    "/change-password",
)
def change_password(
    payload: schemas.PasswordChange,
    current_user: models.User = Depends(
        get_current_user,
    ),
    db: Session = Depends(get_db),
):
    valid = verify_password(
        payload.current_password,
        current_user.hashed_password,
    )

    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    current_user.hashed_password = hash_password(
        payload.new_password,
    )

    db.commit()

    return {
        "detail": "Password updated successfully."
    }


@router.post("/forgot-password")
def forgot_password(
    payload: schemas.ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    email = str(payload.email).strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No registered GymAI account was found for this email.",
        )

    # Prevent rapid repeated sends for the same account.
    latest = (
        db.query(models.PasswordResetCode)
        .filter(models.PasswordResetCode.user_id == user.id)
        .order_by(models.PasswordResetCode.created_at.desc())
        .first()
    )
    if latest and (datetime.now(timezone.utc) - latest.created_at.replace(tzinfo=timezone.utc)).total_seconds() < settings.password_reset_resend_seconds:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {settings.password_reset_resend_seconds} seconds before requesting another code.",
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()

    reset = models.PasswordResetCode(
        user_id=user.id,
        code_hash=code_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.password_reset_expire_minutes),
        attempts=0,
        used=False,
    )
    db.add(reset)
    try:
        send_password_reset_code(user.email, code)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We could not send the verification email. Please try again later.",
        ) from exc

    db.commit()
    return {"detail": "A verification code was sent to your registered email."}


@router.post("/verify-reset-code")
def verify_reset_code(
    payload: schemas.PasswordResetVerification,
    db: Session = Depends(get_db),
):
    email = str(payload.email).strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification code.")

    reset = (
        db.query(models.PasswordResetCode)
        .filter(
            models.PasswordResetCode.user_id == user.id,
            models.PasswordResetCode.used == False,
        )
        .order_by(models.PasswordResetCode.created_at.desc())
        .first()
    )

    if not reset:
        raise HTTPException(status_code=400, detail="No active verification code. Request a new one.")

    now = datetime.now(timezone.utc)
    expires = reset.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        raise HTTPException(status_code=400, detail="This verification code has expired. Request a new one.")

    if reset.attempts >= 5:
        reset.used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Too many incorrect attempts. Request a new code.")

    supplied_hash = hashlib.sha256(payload.code.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(supplied_hash, reset.code_hash):
        reset.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="Incorrect verification code.")

    return {"detail": "Verification code confirmed."}


@router.post("/reset-password")
def reset_password(
    payload: schemas.VerifyResetCode,
    db: Session = Depends(get_db),
):
    email = str(payload.email).strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification code.")

    reset = (
        db.query(models.PasswordResetCode)
        .filter(
            models.PasswordResetCode.user_id == user.id,
            models.PasswordResetCode.used == False,
        )
        .order_by(models.PasswordResetCode.created_at.desc())
        .first()
    )

    if not reset:
        raise HTTPException(status_code=400, detail="No active verification code. Request a new one.")

    now = datetime.now(timezone.utc)
    expires = reset.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        raise HTTPException(status_code=400, detail="This verification code has expired. Request a new one.")

    if reset.attempts >= 5:
        reset.used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Too many incorrect attempts. Request a new code.")

    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match.")

    supplied_hash = hashlib.sha256(payload.code.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(supplied_hash, reset.code_hash):
        reset.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="Incorrect verification code.")

    user.hashed_password = hash_password(payload.new_password)
    reset.used = True
    db.commit()

    return {"detail": "Password reset successfully. You can now log in."}


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
)
def logout(
    current_user: models.User = Depends(
        get_current_user,
    ),
):
    return {
        "detail": "Logged out."
    }

def _verify_google_credential(credential: str) -> dict:
    """Validate a Google ID token with Google's tokeninfo endpoint and check it was issued for this app."""
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured (GOOGLE_CLIENT_ID missing).")
    url = "https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(credential, safe="")
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            info = json.loads(response.read().decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=401, detail="Google sign-in failed. Please try again.")
    if info.get("aud") != settings.google_client_id:
        raise HTTPException(status_code=401, detail="Google sign-in was issued for a different app.")
    if str(info.get("email_verified")).lower() != "true" or not info.get("email"):
        raise HTTPException(status_code=401, detail="Your Google email is not verified.")
    return info


@router.post("/google", response_model=schemas.TokenResponse)
def google_login(payload: schemas.GoogleLoginIn, db: Session = Depends(get_db)):
    """Log in (or create the account on first use) with a Google ID token."""
    info = _verify_google_credential(payload.credential)
    email = str(info["email"]).strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        picture = str(info.get("picture") or "")
        user = models.User(
            full_name=(str(info.get("name") or email.split("@")[0]).strip() or "GymAI Member")[:120],
            email=email,
            hashed_password=GOOGLE_ONLY_PREFIX + secrets.token_hex(16),  # cannot match any typed password
            avatar_url=picture if 0 < len(picture) < 500 else None,
            profile_completed=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(subject=str(user.id))
    return schemas.TokenResponse(access_token=token, user=serialize_user(user))



def _google_user_from_id_token(id_token: str) -> dict:
    return _verify_google_credential(id_token)


def _oauth_state() -> str:
    nonce = secrets.token_urlsafe(24)
    sig = hmac.new(settings.secret_key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return nonce + "." + sig


def _valid_oauth_state(state: str) -> bool:
    try:
        nonce, sig = state.rsplit(".", 1)
        expected = hmac.new(settings.secret_key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
        return bool(nonce) and hmac.compare_digest(sig, expected)
    except ValueError:
        return False


@router.get("/google/start")
def google_start():
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    params = {"client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri, "response_type": "code", "scope": "openid email profile", "access_type": "offline", "prompt": "select_account", "state": _oauth_state()}
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params))


@router.get("/google/callback")
def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    if not _valid_oauth_state(state):
        raise HTTPException(status_code=400, detail="Invalid Google OAuth state.")
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    data = urllib.parse.urlencode({"code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret, "redirect_uri": settings.google_redirect_uri, "grant_type": "authorization_code"}).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, headers={"Content-Type":"application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response: token_info = json.loads(response.read().decode())
        info = _google_user_from_id_token(token_info["id_token"])
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Google authorization failed. Please try again.") from exc
    email = str(info["email"]).strip().lower(); user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        picture = str(info.get("picture") or "")
        user = models.User(full_name=(str(info.get("name") or email.split("@")[0]).strip() or "GymAI Member")[:120], email=email, hashed_password=GOOGLE_ONLY_PREFIX+secrets.token_hex(16), avatar_url=picture if 0 < len(picture) < 500 else None, profile_completed=False)
        db.add(user); db.commit(); db.refresh(user)
    token = create_access_token(subject=str(user.id))
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/auth/callback?token={urllib.parse.quote(token)}")

@router.get("/email-status")
def email_status(email: str, db: Session = Depends(get_db)):
    """Lets the Create-account page switch to "enter your password" when the email is already registered."""
    user = db.query(models.User).filter(models.User.email == email.strip().lower()).first()
    return {
        "exists": user is not None,
        "google_only": bool(user and user.hashed_password.startswith(GOOGLE_ONLY_PREFIX)),
    }

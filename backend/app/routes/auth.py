"""Authentication routes: register, login, session, editor-mode, me"""
from fastapi import APIRouter, Depends, HTTPException, Request
from contextlib import suppress
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from backend.app.models import NotificationLog, User, UserRole
from backend.app.schemas import TokenSchema, UserCreate, UserSchema
from backend.app.core.shared import (
    _audit,
    _MIN_PASSWORD_LEN,
    _request_payload,
    _request_user_and_role,
    _send_login_alert,
    _ist_wall,
)

router = APIRouter()


@router.post("/auth/register", response_model=UserSchema)
def register(user: UserCreate, request: Request, db: Session = Depends(get_db)):
    """Register a new user.

    This route is reachable without a token only while the users table is
    empty (enforced in rbac_guard). That makes it the bootstrap for a brand
    new installation, so the first account is forced to admin here rather
    than taken from the request body. Two reasons:

    * The schema defaults role to viewer, so a client that omitted the field
      produced a read-only first user. Because /auth/register then closes and
      promoting a user needs admin rights, that install was permanently
      locked out with no way back in.
    * Trusting the body meant the one unauthenticated write in the app also
      chose its own privilege level.
    """
    # Bootstrap: first account in an empty database is always an admin.
    is_first_user = db.query(User).count() == 0
    role = UserRole.ADMIN if is_first_user else user.role

    # Check if user exists
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered. Sign in instead, or pick a different username.")

    db_user = db.query(User).filter(User.email == user.email).first() if user.email else None
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered. Sign in instead, or use a different email.")

    # Create user
    hashed_pw = get_password_hash(user.password)
    db_user = User(
        username=user.username,
        email=user.email,
        hashed_password=hashed_pw,
        full_name=user.full_name,
        role=role,
    )
    db.add(db_user)
    _audit(
        db,
        request,
        "users",
        "create",
        record=db_user,
        record_type="user",
        new_values={
            "username": user.username,
            "role": str(getattr(user.role, "value", user.role) or ""),
        },
    )
    db.commit()
    db.refresh(db_user)
    return db_user


@router.post("/auth/login", response_model=TokenSchema)
def login(username: str, password: str, request: Request, db: Session = Depends(get_db)):
    """Login and get access token"""
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        db.add(
            NotificationLog(
                recipient=username[:200] if username else "unknown",
                message=f"Failed sign-in attempt for '{username}'",
                status="auth-failed",
            )
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    access_token = create_access_token(data={"sub": user.username})
    # Record the sign-in: failed attempts already land in the notification
    # log, and this row doubles as the session start for session-scoped
    # visibility (see _latest_login_at). One row per login is cheap.
    _audit(
        db,
        request,
        "users",
        "login",
        record=user,
        record_type="user",
        new_values={"username": user.username},
        notes=f"Sign-in: {user.username}",
    )
    db.commit()
    # Sign-in alert (best-effort, never blocks login): whoever holds this
    # account's alert address learns about every fresh sign-in, so a stolen
    # password cannot be used quietly. Misconfigured or absent mail settings
    # simply mean no mail goes out.
    with suppress(Exception):
        _send_login_alert(db, request, user.username)
    # No `editor` claim: Editor Mode is off on every fresh sign-in.
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/auth/session")
def record_session(request: Request, db: Session = Depends(get_db)):
    """Record an app start on a restored (remembered-token) session.

    A relaunch with a still-valid stored token never hits /auth/login, so
    without this there would be no session-start row and session-scoped
    visibility would silently fall back to showing everything. Called once
    by the frontend on the restore path only; fresh logins already audit.
    """
    user, _role = _request_user_and_role(db, request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    _audit(
        db,
        request,
        "users",
        "login",
        record=user,
        record_type="user",
        new_values={"username": user.username},
        notes=f"Session restored: {user.username}",
    )
    db.commit()
    return {"status": "recorded"}


@router.post("/auth/editor-mode", response_model=TokenSchema)
def set_editor_mode(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Switch Editor Mode on or off for this session and return a fresh token.

    Editor Mode is a second gate in front of role permissions: a user may hold
    edit and delete rights and still be refused until they deliberately switch
    it on. The state lives in a signed token claim rather than the database, so
    it cannot be forged client-side, it expires with the session, and signing in
    again always starts with it off.

    The original expiry is preserved so toggling cannot extend a session.
    """

    enabled = bool(payload.get("enabled"))
    claims = _request_payload(request)
    if not claims or not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = db.query(User).filter(User.username == claims["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = {k: v for k, v in claims.items() if k != "editor"}
    if enabled:
        data["editor"] = True

    from datetime import datetime, timezone
    expires_delta = None
    exp = claims.get("exp")
    if exp:
        try:
            # exp is a POSIX timestamp (UTC): remaining lifetime is
            # expiry minus now. Reversed, every live session reads as
            # already expired and the toggle can never switch on.
            # (One expression per line: the IST audit test exempts lines
            # mentioning expires_delta as pure relative UTC arithmetic.)
            exp_dt = datetime.fromtimestamp(float(exp), tz=timezone.utc)
            expires_delta = exp_dt - datetime.now(timezone.utc)
            if expires_delta.total_seconds() <= 0:
                raise HTTPException(status_code=401, detail="Session expired")
        except (TypeError, ValueError, OSError, OverflowError):
            expires_delta = None

    token = create_access_token(data=data, expires_delta=expires_delta)
    _audit(
        db,
        request,
        "users",
        "editor_mode",
        record=user,
        record_type="user",
        old_values={"editor_mode": bool(claims.get("editor"))},
        new_values={"editor_mode": enabled},
        notes="Editor Mode switched " + ("on" if enabled else "off"),
    )
    db.commit()
    return {"access_token": token, "token_type": "bearer", "editor_mode": enabled}


@router.get("/auth/me", response_model=UserSchema)
def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Return the user for a valid Bearer token (used for session display)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(auth[len("Bearer ") :].strip())
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.query(User).filter(User.username == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user
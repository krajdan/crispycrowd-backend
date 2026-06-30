from fastapi import FastAPI, Depends, HTTPException, status, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, ForeignKey, Float, func
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import JWTError, jwt
import os
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─────────────────────────────────────────────
# 1. KONFIGURATION
# ─────────────────────────────────────────────
DATABASE_URL         = os.getenv("DATABASE_URL", "sqlite:///./crispcrowd.db")
SECRET_KEY           = os.getenv("SECRET_KEY", "byt-ut-denna-i-produktion-minst-32-tecken")
ALGORITHM            = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
SMTP_HOST            = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT            = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER            = os.getenv("SMTP_USER", "")
SMTP_PASS            = os.getenv("SMTP_PASS", "")
FRONTEND_URL         = os.getenv("FRONTEND_URL", "http://localhost:5173")
ADMIN_EMAIL          = os.getenv("ADMIN_EMAIL", "krajdan@gmail.com")

# ─────────────────────────────────────────────
# 2. DATABAS
# ─────────────────────────────────────────────
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine       = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base         = declarative_base()

# ─────────────────────────────────────────────
# 3. SÄKERHET
# ─────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain, hashed):  return pwd_context.verify(plain, hashed)
def get_password_hash(pw):           return pwd_context.hash(pw)
def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode["exp"] = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ─────────────────────────────────────────────
# 4. MODELLER
# ─────────────────────────────────────────────
class UserDB(Base):
    __tablename__ = "users"
    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String, index=True)
    email           = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    reset_token     = Column(String, nullable=True)
    reset_expires   = Column(String, nullable=True)

class RecipeDB(Base):
    __tablename__ = "recipes"
    id           = Column(Integer, primary_key=True, index=True)
    title        = Column(String, index=True)
    description  = Column(Text)
    category     = Column(String, default="Huvudrätt")
    image        = Column(Text)
    time         = Column(String)
    temp         = Column(String)
    servings     = Column(Integer, default=1)
    ingredients  = Column(Text)
    instructions = Column(Text)
    owner_id     = Column(Integer, ForeignKey("users.id"))
    reported     = Column(Boolean, default=False)

class RecipeRatingDB(Base):
    __tablename__ = "recipe_ratings"
    id        = Column(Integer, primary_key=True, index=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"))
    user_id   = Column(Integer, ForeignKey("users.id"))
    rating    = Column(Integer)   # 1–5

Base.metadata.create_all(bind=engine)

# ─────────────────────────────────────────────
# 5. SCHEMAS
# ─────────────────────────────────────────────
class UserAuth(BaseModel):
    name: Optional[str] = None
    email: str
    password: str

class RecipeBase(BaseModel):
    title: str
    description: str
    category: Optional[str]  = "Huvudrätt"
    image: Optional[str]     = ""
    time: Optional[str]      = ""
    temp: Optional[str]      = ""
    servings: Optional[int]  = 1
    ingredients: str
    instructions: str
    reported: Optional[bool] = False

class RecipeCreate(RecipeBase):
    pass

class RecipeResponse(RecipeBase):
    id:           int
    owner_id:     int
    owner_name:   str
    avg_rating:   float
    rating_count: int
    my_rating:    Optional[int] = None   # inloggad användares röst

class RateRequest(BaseModel):
    rating: int   # 1–5

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

# ─────────────────────────────────────────────
# 6. APP & CORS
# ─────────────────────────────────────────────
app = FastAPI(title="CrispCrowd API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:    yield db
    finally: db.close()

def get_current_user(request: Request, db: Session = Depends(get_db)):
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Kunde inte validera uppgifter")
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "): raise exc
    try:
        payload = jwt.decode(auth.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
        uid = payload.get("sub")
        if uid is None: raise exc
    except JWTError: raise exc
    user = db.query(UserDB).filter(UserDB.id == int(uid)).first()
    if user is None: raise exc
    return user

def get_optional_user(request: Request, db: Session = Depends(get_db)):
    """Returnerar inloggad användare ELLER None – används på publika routes."""
    try:    return get_current_user(request, db)
    except: return None

# ─────────────────────────────────────────────
# 7. HJÄLP: bygg RecipeResponse med snitt + ägarnamn
# ─────────────────────────────────────────────
def build_recipe_response(recipe: RecipeDB, db: Session, current_user_id: Optional[int] = None) -> dict:
    agg = db.query(
        func.avg(RecipeRatingDB.rating).label("avg"),
        func.count(RecipeRatingDB.id).label("cnt")
    ).filter(RecipeRatingDB.recipe_id == recipe.id).first()

    my_rating = None
    if current_user_id:
        row = db.query(RecipeRatingDB).filter(
            RecipeRatingDB.recipe_id == recipe.id,
            RecipeRatingDB.user_id   == current_user_id
        ).first()
        if row: my_rating = row.rating

    owner = db.query(UserDB).filter(UserDB.id == recipe.owner_id).first()

    return {
        **{c.name: getattr(recipe, c.name) for c in recipe.__table__.columns},
        "owner_name":   owner.name if owner else "Okänd",
        "avg_rating":   round(float(agg.avg), 1) if agg.avg else 0.0,
        "rating_count": agg.cnt or 0,
        "my_rating":    my_rating,
    }

# ─────────────────────────────────────────────
# 8. E-POST
# ─────────────────────────────────────────────
def send_reset_email(to_email: str, reset_link: str):
    if not SMTP_USER or not SMTP_PASS:
        print(f"\n[DEV] Återställningslänk för {to_email}:\n{reset_link}\n")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Återställ ditt CrispCrowd-lösenord"
    msg["From"]    = f"CrispCrowd <{SMTP_USER}>"
    msg["To"]      = to_email
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px">
      <h2 style="color:#E48268">Glömt lösenordet?</h2>
      <p>Klicka nedan för att välja ett nytt lösenord. Länken gäller i <strong>30 minuter</strong>.</p>
      <a href="{reset_link}" style="display:inline-block;margin:24px 0;padding:14px 28px;
         background:#E48268;color:#fff;border-radius:99px;font-weight:bold;text-decoration:none">
        Återställ lösenord
      </a>
      <p style="color:#888;font-size:12px">Fick du mailet av misstag? Ignorera det.</p>
    </div>"""
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(); s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, to_email, msg.as_string())

# ─────────────────────────────────────────────
# 9. AUTH-ROUTES
# ─────────────────────────────────────────────
@app.post("/api/auth/register")
def register(user: UserAuth, db: Session = Depends(get_db)):
    if len(user.password) > 72:
        raise HTTPException(status_code=400, detail="Lösenordet får vara max 72 tecken.")
    if db.query(UserDB).filter(UserDB.email == user.email).first():
        raise HTTPException(status_code=400, detail="E-postadressen är redan registrerad")
    new_user = UserDB(name=user.name or "Anonym kock", email=user.email,
                      hashed_password=get_password_hash(user.password))
    db.add(new_user); db.commit(); db.refresh(new_user)
    token = create_access_token({"sub": str(new_user.id)})
    return {"token": token, "user": {"id": new_user.id, "name": new_user.name, "email": new_user.email}}

@app.post("/api/auth/login")
def login(user: UserAuth, db: Session = Depends(get_db)):
    db_user = db.query(UserDB).filter(UserDB.email == user.email).first()
    if not db_user or not verify_password(user.password, db_user.hashed_password):
        raise HTTPException(status_code=400, detail="Fel e-post eller lösenord")
    token = create_access_token({"sub": str(db_user.id)})
    return {"token": token, "user": {"id": db_user.id, "name": db_user.name, "email": db_user.email}}

@app.get("/api/auth/me")
def get_me(current_user: UserDB = Depends(get_current_user)):
    return {"id": current_user.id, "name": current_user.name, "email": current_user.email}

@app.delete("/api/auth/me")
def delete_account(current_user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(RecipeRatingDB).filter(RecipeRatingDB.user_id == current_user.id).delete()
    db.query(RecipeDB).filter(RecipeDB.owner_id == current_user.id).delete()
    db.delete(current_user); db.commit()
    return {"message": "Kontot och alla tillhörande recept har raderats."}

@app.post("/api/auth/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.email == body.email).first()
    if user:
        token   = secrets.token_urlsafe(32)
        expires = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
        user.reset_token = token; user.reset_expires = expires; db.commit()
        send_reset_email(user.email, f"{FRONTEND_URL}?token={token}")
    return {"message": "Om adressen finns hos oss skickas ett återställningsmail."}

@app.post("/api/auth/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.reset_token == body.token).first()
    if not user or not user.reset_expires:
        raise HTTPException(status_code=400, detail="Ogiltig eller utgången länk.")
    if datetime.utcnow() > datetime.fromisoformat(user.reset_expires):
        raise HTTPException(status_code=400, detail="Länken har gått ut. Begär en ny.")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Lösenordet måste vara minst 6 tecken.")
    user.hashed_password = get_password_hash(body.new_password)
    user.reset_token = None; user.reset_expires = None; db.commit()
    return {"message": "Lösenordet är nu uppdaterat. Du kan logga in."}

# ─────────────────────────────────────────────
# 10. RECEPT-ROUTES
# ─────────────────────────────────────────────

@app.get("/api/recipes/featured")
def get_featured_recipe(request: Request, db: Session = Depends(get_db)):
    """Returnerar ett slumpmässigt recept bland de 5 högst betygsatta."""
    import random
    current_user = get_optional_user(request, db)
    uid = current_user.id if current_user else None

    all_recipes = db.query(RecipeDB).all()
    if not all_recipes:
        raise HTTPException(status_code=404, detail="Inga recept finns ännu.")

    with_ratings = []
    for r in all_recipes:
        agg = db.query(
            func.avg(RecipeRatingDB.rating).label("avg"),
        ).filter(RecipeRatingDB.recipe_id == r.id).first()
        avg = float(agg.avg) if agg.avg else 0.0
        with_ratings.append((r, avg))

    with_ratings.sort(key=lambda x: x[1], reverse=True)
    top5 = with_ratings[:5]
    chosen, _ = random.choice(top5)
    return build_recipe_response(chosen, db, uid)

@app.get("/api/recipes")
def get_all_recipes(request: Request, db: Session = Depends(get_db)):
    current_user = get_optional_user(request, db)
    uid = current_user.id if current_user else None
    recipes = db.query(RecipeDB).order_by(RecipeDB.id.desc()).all()
    return [build_recipe_response(r, db, uid) for r in recipes]

@app.get("/api/recipes/search")
def search_recipes(
    request: Request,
    db: Session = Depends(get_db),
    q:          Optional[str]   = Query(None, description="Fritextsök i titel, beskrivning, ingredienser"),
    category:   Optional[str]   = Query(None),
    owner_name: Optional[str]   = Query(None, description="Filtrera på kockens namn"),
    min_rating: Optional[float] = Query(None, description="Minsta snittbetyg"),
    max_time:   Optional[int]   = Query(None, description="Max tillagningstid i minuter"),
):
    current_user = get_optional_user(request, db)
    uid = current_user.id if current_user else None

    query = db.query(RecipeDB)

    # Kategori
    if category and category != "Alla":
        query = query.filter(RecipeDB.category == category)

    # Fritext – söker i titel, beskrivning OCH ingredienser
    if q:
        term = f"%{q.lower()}%"
        from sqlalchemy import or_
        query = query.filter(or_(
            func.lower(RecipeDB.title).like(term),
            func.lower(RecipeDB.description).like(term),
            func.lower(RecipeDB.ingredients).like(term),
        ))

    # Filtrera på kockens namn
    if owner_name:
        matching_users = db.query(UserDB.id).filter(
            func.lower(UserDB.name).like(f"%{owner_name.lower()}%")
        ).all()
        owner_ids = [u.id for u in matching_users]
        query = query.filter(RecipeDB.owner_id.in_(owner_ids))

    # Max tid – extrahera siffran ur "20 min" eller "20m"
    if max_time:
        import re
        all_recipes = query.all()
        filtered = []
        for r in all_recipes:
            if r.time:
                nums = re.findall(r'\d+', r.time)
                if nums and int(nums[0]) <= max_time:
                    filtered.append(r)
            # Recept utan tid inkluderas alltid
        results = [build_recipe_response(r, db, uid) for r in filtered]
    else:
        results = [build_recipe_response(r, db, uid) for r in query.order_by(RecipeDB.id.desc()).all()]

    # Filtrera på min betyg (efter att snitt räknats ut)
    if min_rating:
        results = [r for r in results if r["avg_rating"] >= min_rating]

    return results

@app.post("/api/recipes")
def create_recipe(recipe: RecipeCreate, db: Session = Depends(get_db),
                  current_user: UserDB = Depends(get_current_user)):
    new_recipe = RecipeDB(**recipe.dict(), owner_id=current_user.id)
    db.add(new_recipe); db.commit(); db.refresh(new_recipe)
    return build_recipe_response(new_recipe, db, current_user.id)

@app.put("/api/recipes/{recipe_id}")
def update_recipe(recipe_id: int, recipe_update: RecipeCreate,
                  db: Session = Depends(get_db),
                  current_user: UserDB = Depends(get_current_user)):
    db_recipe = db.query(RecipeDB).filter(RecipeDB.id == recipe_id).first()
    if not db_recipe:
        raise HTTPException(status_code=404, detail="Receptet hittades inte")
    for key, value in recipe_update.dict().items():
        setattr(db_recipe, key, value)
    db.commit(); db.refresh(db_recipe)
    return build_recipe_response(db_recipe, db, current_user.id)

@app.delete("/api/recipes/{recipe_id}")
def delete_recipe(recipe_id: int, db: Session = Depends(get_db),
                  current_user: UserDB = Depends(get_current_user)):
    db_recipe = db.query(RecipeDB).filter(RecipeDB.id == recipe_id).first()
    if not db_recipe:
        raise HTTPException(status_code=404, detail="Receptet hittades inte")
    if db_recipe.owner_id != current_user.id and current_user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Du har inte behörighet att ta bort detta recept")
    db.query(RecipeRatingDB).filter(RecipeRatingDB.recipe_id == recipe_id).delete()
    db.delete(db_recipe); db.commit()
    return {"message": "Receptet är borttaget"}

# ─────────────────────────────────────────────
# 11. BETYG-ROUTE
# ─────────────────────────────────────────────
@app.post("/api/recipes/{recipe_id}/rate")
def rate_recipe(recipe_id: int, body: RateRequest,
                db: Session = Depends(get_db),
                current_user: UserDB = Depends(get_current_user)):
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(status_code=400, detail="Betyg måste vara mellan 1 och 5.")
    recipe = db.query(RecipeDB).filter(RecipeDB.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Receptet hittades inte")

    existing = db.query(RecipeRatingDB).filter(
        RecipeRatingDB.recipe_id == recipe_id,
        RecipeRatingDB.user_id   == current_user.id
    ).first()

    if existing:
        existing.rating = body.rating   # uppdatera befintlig röst
    else:
        db.add(RecipeRatingDB(recipe_id=recipe_id, user_id=current_user.id, rating=body.rating))

    db.commit()
    return build_recipe_response(recipe, db, current_user.id)

# ─────────────────────────────────────────────
# 12. HEALTH CHECK
# ─────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "ok", "service": "CrispCrowd API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")), reload=True)
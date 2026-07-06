from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pathlib import Path
from .config import get_settings

settings = get_settings()

# sqlite の相対パスをこのファイルの場所基準で絶対パスに変換
db_url = settings.database_url
if db_url.startswith("sqlite:///./"):
    db_path = Path(__file__).parent.parent / db_url.replace("sqlite:///./", "")
    db_url = f"sqlite:///{db_path}"

engine = create_engine(
    db_url,
    connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

from sqlmodel import Field, SQLModel, create_engine, Session, select
from typing import Optional
from datetime import datetime
from config import DB_PATH

class AudioRecord(SQLModel, table=True):
    audio_id: str = Field(primary_key=True)
    audio_link: str
    usage_count: int
    duration: int
    original_video_link: Optional[str] = None
    year: int
    recent_usage: int = 0
    source_type: Optional[str] = None
    status: str = "pending" # pending, passed, rejected
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ViewedVideo(SQLModel, table=True):
    video_link: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ShazamCache(SQLModel, table=True):
    audio_id: str = Field(primary_key=True)
    is_copyrighted: bool
    track_title: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

engine = create_engine(f"sqlite:///{DB_PATH}")

def init_db():
    SQLModel.metadata.create_all(engine)

def get_all_viewed_videos():
    with Session(engine) as session:
        return set(session.exec(select(ViewedVideo.video_link)).all())

def get_all_audio_ids():
    with Session(engine) as session:
        return set(session.exec(select(AudioRecord.audio_id)).all())

def is_video_viewed(video_link: str):
    with Session(engine) as session:
        statement = select(ViewedVideo).where(ViewedVideo.video_link == video_link)
        return session.exec(statement).first() is not None

def save_viewed_video(video_link: str):
    with Session(engine) as session:
        # Dùng try-except để tránh crash nếu trùng (dù đã check set)
        try:
            session.add(ViewedVideo(video_link=video_link))
            session.commit()
        except:
            session.rollback()

def save_audio(record: AudioRecord):
    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)

def get_audio(audio_id: str):
    with Session(engine) as session:
        statement = select(AudioRecord).where(AudioRecord.audio_id == audio_id)
        return session.exec(statement).first()

def get_shazam_cache(audio_id: str):
    with Session(engine) as session:
        return session.get(ShazamCache, audio_id)

def save_shazam_cache(audio_id: str, is_copyrighted: bool, title: str = None):
    with Session(engine) as session:
        cache = ShazamCache(audio_id=audio_id, is_copyrighted=is_copyrighted, track_title=title)
        session.add(cache)
        session.commit()

def update_audio_status(audio_id: str, status: str, reason: str = None):
    with Session(engine) as session:
        statement = select(AudioRecord).where(AudioRecord.audio_id == audio_id)
        record = session.exec(statement).first()
        if record:
            record.status = status
            record.rejection_reason = reason
            session.add(record)
            session.commit()


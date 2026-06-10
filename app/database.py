from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Lead(Base):
    __tablename__ = "leads"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    company = Column(String)
    industry = Column(String)
    country = Column(String)
    email = Column(String)
    message = Column(Text)
    status = Column(String, default="waiting")
    opened = Column(Boolean, default=False)

def init_db():
    Base.metadata.create_all(bind=engine)
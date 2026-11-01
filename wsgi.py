import os

os.environ.setdefault('SOBHE_ENV', 'production')
from app import app as application

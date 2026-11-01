"""Production server; run behind an HTTPS reverse proxy."""
import os
from waitress import serve
from app import app

if __name__ == '__main__':
    serve(app, host=os.environ.get('SOBHE_HOST', '127.0.0.1'),
          port=int(os.environ.get('SOBHE_PORT', '8000')))

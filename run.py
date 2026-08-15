"""
Development entry point.
Production: gunicorn -w 2 -b 127.0.0.1:8000 'app:create_app()'
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
